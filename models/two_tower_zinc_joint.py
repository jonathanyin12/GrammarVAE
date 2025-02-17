import copy
from keras import backend as K
from keras.losses import binary_crossentropy, categorical_crossentropy, mse
from keras.models import Model
from keras.layers import Input, Dense, Lambda, Concatenate, Reshape
from keras.layers.core import Dense, Activation, Flatten, RepeatVector
from keras.layers.wrappers import TimeDistributed
from keras.layers.recurrent import GRU
from keras.layers.convolutional import Convolution1D
import tensorflow as tf
import zinc_grammar as G

masks_K = K.variable(G.masks)
ind_of_ind_K = K.variable(G.ind_of_ind)

MAX_LEN = 277
MAX_LEN_FUNCTIONAL = 200
DIM = G.D


class MoleculeVAE():
    autoencoder = None

    def create(self,
               charset,
               max_length=MAX_LEN,
               max_length_functional=MAX_LEN_FUNCTIONAL,
               latent_rep_size=2,
               weights_file=None):
        charset_length = len(charset)

        x = Input(shape=(max_length, charset_length))
        f = Input(shape=(max_length_functional, 1))

        _, z = self._buildEncoder(x, f, latent_rep_size, max_length, max_length_functional)
        self.encoder = Model([x, f], z)

        encoded_input = Input(shape=(latent_rep_size,))
        o, fo, po = self._buildDecoder(
            encoded_input,
            latent_rep_size,
            max_length,
            max_length_functional,
            charset_length
        )
        self.decoder = Model(
            encoded_input,
            [o, fo, po]
        )

        x1 = Input(shape=(max_length, charset_length))
        f1 = Input(shape=(max_length_functional, 1))
        vae_loss, z1 = self._buildEncoder(x1, f1, latent_rep_size, max_length, max_length_functional)
        o1, fo1, po1 = self._buildDecoder(
            z1,
            latent_rep_size,
            max_length,
            max_length_functional,
            charset_length
        )
        self.autoencoder = Model(
            [x1, f1],
            [o1, fo1, po1]
        )

        # for obtaining mean and log variance of encoding distribution
        x2 = Input(shape=(max_length, charset_length))
        f2 = Input(shape=(max_length_functional, 1))
        (z_m, z_l_v) = self._encoderMeanVar(x2, f2, latent_rep_size, max_length, max_length_functional)
        self.encoderMV = Model(inputs=[x2, f2], outputs=[z_m, z_l_v])

        if weights_file:
            self.autoencoder.load_weights(weights_file)
            self.encoder.load_weights(weights_file, by_name=True)
            self.decoder.load_weights(weights_file, by_name=True)
            self.encoderMV.load_weights(weights_file, by_name=True)

        self.autoencoder.compile(optimizer='Adam',
                                 loss={'decoded_mean': vae_loss, 'decoded_mean_2': vae_loss, 'property_loss': vae_loss},
                                 metrics=['accuracy'])

    # Encoder tower structure
    def _towers(self, x, f, max_length, max_length_func):
        # Tower 1
        h = Convolution1D(9, 9, activation='relu', name='conv_1')(x)
        h = Convolution1D(9, 9, activation='relu', name='conv_2')(h)
        h = Convolution1D(10, 11, activation='relu', name='conv_3')(h)
        h = Flatten(name='flatten_1')(h)

        # Tower 2
        hf = Flatten(name='tower_2_flatten_enc')(f)
        hf = Dense(256, activation='relu', name='tower_2_dense_1')(hf)
        #         hf = Flatten(name='tower_2_flatten_1')(hf)

        # Merge
        h = Concatenate()([h, hf])
        return Dense(435, activation='relu', name='dense_1')(h)

    def _encoderMeanVar(self, x, f, latent_rep_size, max_length, max_length_func, epsilon_std=0.01):
        h = self._towers(x, f, max_length, max_length_func)

        z_mean = Dense(latent_rep_size, name='z_mean', activation='linear')(h)
        z_log_var = Dense(latent_rep_size, name='z_log_var', activation='linear')(h)

        return (z_mean, z_log_var)

    def _buildEncoder(self, x, f, latent_rep_size, max_length, max_length_func, epsilon_std=0.01):
        h = self._towers(x, f, max_length, max_length_func)

        def sampling(args):
            z_mean_, z_log_var_ = args
            batch_size = K.shape(z_mean_)[0]
            epsilon = K.random_normal(shape=(batch_size, latent_rep_size), mean=0., stddev=epsilon_std)
            return z_mean_ + K.exp(z_log_var_ / 2) * epsilon

        z_mean = Dense(latent_rep_size, name='z_mean', activation='linear')(h)
        z_log_var = Dense(latent_rep_size, name='z_log_var', activation='linear')(h)

        # this function is the main change.
        # essentially we mask the training data so that we are only allowed to apply
        #   future rules based on the current non-terminal
        def conditional(x_true, x_pred, max_l, charset_l):
            most_likely = K.argmax(x_true)
            print('most_likely', K.int_shape(most_likely))
            most_likely = tf.reshape(most_likely, [-1])  # flatten most_likely
            print('most_likely', K.int_shape(most_likely))
            ix2 = tf.expand_dims(tf.gather(ind_of_ind_K, most_likely), 1)  # index ind_of_ind with res
            print('ix2', K.int_shape(ix2))
            ix2 = tf.cast(ix2, tf.int32)  # cast indices as ints
            print('ix2', K.int_shape(ix2))
            M2 = tf.gather_nd(masks_K, ix2)  # get slices of masks_K with indices
            print('M2', K.int_shape(M2))
            M3 = tf.reshape(M2, [-1, max_l, charset_l])  # reshape them
            print('M3', K.int_shape(M3))
            P2 = tf.multiply(K.exp(x_pred), M3)  # apply them to the exp-predictions
            print('P2', K.int_shape(P2))
            P2 = tf.divide(P2, K.sum(P2, axis=-1, keepdims=True))  # normalize predictions
            print('P2', K.int_shape(P2))
            return P2

        def vae_loss(true, pred_decoded_mean):
            print('vae_loss', K.int_shape(true))
            print('vae_loss', K.int_shape(true[0]))
            print('vae_loss_2', K.int_shape(pred_decoded_mean))
            # print('vae_loss_3', K.int_shape(pred_functional))

            if K.int_shape(pred_decoded_mean)[1] == max_length:
                x_decoded_mean = conditional(true, pred_decoded_mean, max_length,
                                             DIM)  # we add this new function to the loss
                x = K.flatten(true)
                x_decoded_mean = K.flatten(x_decoded_mean)
                xent_loss = max_length * binary_crossentropy(x, x_decoded_mean)
            elif K.int_shape(pred_decoded_mean)[1] == max_length_func:
                # f_decoded_mean = conditional(true, pred_decoded_mean, max_length_func, 1) # we add this new function to the loss
                # f = tf.reshape(true, (-1, max_length_func))
                # f_decoded_mean = tf.reshape(f_decoded_mean, (-1, max_length_func))
                # xent_loss = max_length_func * binary_crossentropy(f, f_decoded_mean)

                t = tf.reshape(true, (-1, max_length_func))
                p = tf.reshape(pred_decoded_mean, (-1, max_length_func))
                xent_loss = max_length_func * mse(t, p)

            elif K.int_shape(pred_decoded_mean)[1] == 1:
                t = tf.reshape(true, (-1, 1))
                p = tf.reshape(pred_decoded_mean, (-1, 1))
                xent_loss = mse(t, p)

            else:
                raise ValueError('UNRECOGNIZED SHAPE')

            kl_loss = - 0.5 * K.mean(1 + z_log_var - K.square(z_mean) - K.exp(z_log_var), axis=-1)

            print('kl_loss', K.int_shape(kl_loss))
            print('xe_loss', K.int_shape(xent_loss))
            return xent_loss + kl_loss

        return (vae_loss, Lambda(sampling, output_shape=(latent_rep_size,), name='lambda')([z_mean, z_log_var]))

    def _buildDecoder(self, z, latent_rep_size, max_length, max_length_functional, charset_length):
        l = Dense(latent_rep_size, name='latent_input', activation='relu')(z)

        # Property prediction
        hp = Dense(1024, name='dense_property_tower', activation='relu')(l)
        hp = Dense(1, name='property_loss', activation='linear')(hp)

        # Tower 2
        # hf = Dense(128, name='dense_tower_1', activation = 'relu')(l)
        hf = Dense(200, name='dense_tower_2', activation='sigmoid')(l)
        hf = Reshape((200, 1), name='decoded_mean_2')(hf)

        # Tower 1
        h = RepeatVector(max_length, name='repeat_vector')(l)
        h = GRU(501, return_sequences=True, name='gru_1')(h)
        h = GRU(501, return_sequences=True, name='gru_2')(h)
        h = GRU(501, return_sequences=True, name='gru_3')(h)
        h = TimeDistributed(Dense(charset_length), name='decoded_mean')(h)

        print('build decoder', K.int_shape(h), K.int_shape(hf))

        return h, hf, hp

    def save(self, filename):
        self.autoencoder.save_weights(filename)

    def load(self, charset, weights_file, latent_rep_size=128, max_length=MAX_LEN):
        self.create(charset, max_length=max_length, weights_file=weights_file, latent_rep_size=latent_rep_size)