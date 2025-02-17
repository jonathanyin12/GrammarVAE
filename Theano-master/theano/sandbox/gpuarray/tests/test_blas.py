
from unittest import TestCase
from nose.plugins.skip import SkipTest

import numpy

import theano
from theano import tensor
from theano.tests import unittest_tools as utt
from theano.tensor.blas import gemv_inplace, gemm_inplace, _dot22
from theano.tensor.tests.test_blas import TestGer, BaseGemv

from .. import gpuarray_shared_constructor
from .config import mode_with_gpu
from .test_basic_ops import makeTester, rand

from ..blas import (gpugemv_inplace, gpugemv_no_inplace,
                    gpugemm_inplace,
                    gpuger_inplace, gpuger_no_inplace,
                    GpuGer, gpu_dot22, GpuGemm)


GpuGemvTester = makeTester(
    'GpuGemvTester',
    op=gemv_inplace, gpu_op=gpugemv_inplace,
    cases=dict(dot_vv=[rand(1), 1, rand(1, 2), rand(2), 0],
               dot_vm=[rand(3), 1, rand(3, 2), rand(2), 0],
               # test_02=[rand(0), 1, rand(0, 2), rand(2), 0],
               # test_30=[rand(3), 1, rand(3, 0), rand(0), 0],
               # test_00=[rand(0), 1, rand(0, 0), rand(0), 0],
               test_stride=[rand(3)[::-1], 1, rand(3, 2)[::-1], rand(2)[::-1], 0],
               )
    )


class TestGpuSgemv(TestCase, BaseGemv, utt.TestOptimizationMixin):
    mode = mode_with_gpu
    dtype = 'float32'

    gemv = gpugemv_no_inplace
    gemv_inplace = gpugemv_inplace

    @staticmethod
    def shared(val):
        try:
            return gpuarray_shared_constructor(val)
        except TypeError:
            return theano.shared(val)


GpuGemmTester = makeTester(
    'GpuGemmTester',
    op=gemm_inplace, gpu_op=gpugemm_inplace,
    cases=dict(test1=[rand(3, 4), 1.0, rand(3, 5), rand(5, 4), 0.0],
               test2=[rand(3, 4), 1.0, rand(3, 5), rand(5, 4), 1.0],
               test3=[rand(3, 4), 1.0, rand(3, 5), rand(5, 4), -1.0],
               test4=[rand(3, 4), 0.0, rand(3, 5), rand(5, 4), 0.0],
               test5=[rand(3, 4), 0.0, rand(3, 5), rand(5, 4), 0.6],
               test6=[rand(3, 4), 0.0, rand(3, 5), rand(5, 4), -1.0],
               test7=[rand(3, 4), -1.0, rand(3, 5), rand(5, 4), 0.0],
               test8=[rand(3, 4), -1.0, rand(3, 5), rand(5, 4), 1.1],
               test9=[rand(3, 4), -1.0, rand(3, 5), rand(5, 4), -1.1],
               # test10=[rand(0, 4), -1.0, rand(0, 5), rand(5, 4), 0.0],
               # test11=[rand(3, 0), -1.0, rand(3, 5), rand(5, 0), 1.1],
               # test12=[rand(3, 4), -1.0, rand(3, 0), rand(0, 4), -1.1],
               # test13=[rand(0, 0), -1.0, rand(0, 0), rand(0, 0), -1.1],
               )
    )


class TestGpuSger(TestGer):
    def setUp(self):
        self.mode = mode_with_gpu
        dtype = self.dtype = 'float32'  # optimization isn't dtype-dependent
        self.A = tensor.tensor(dtype=dtype, broadcastable=(False, False))
        self.a = tensor.tensor(dtype=dtype, broadcastable=())
        self.x = tensor.tensor(dtype=dtype, broadcastable=(False,))
        self.y = tensor.tensor(dtype=dtype, broadcastable=(False,))
        self.ger_destructive = gpuger_inplace

        # data on the gpu make the op always inplace
        self.ger = gpuger_inplace
        self.gemm = gpugemm_inplace

    def test_f32_0_0(self):
        raise SkipTest('0-sized objects not supported')

    def test_f32_1_0(self):
        raise SkipTest('0-sized objects not supported')

    def test_f32_0_1(self):
        raise SkipTest('0-sized objects not supported')


class TestGpuSgerNoTransfer(TestGpuSger):
    shared = staticmethod(gpuarray_shared_constructor)


class TestGpuGer_OpContract(TestCase, utt.T_OpContractMixin):
    def setUp(self):
        self.ops = [gpuger_no_inplace, gpuger_inplace]

    def clone(self, op):
        return GpuGer(inplace=op.inplace)


GpuDot22Tester = makeTester(
    'GpuDot22Tester',
    op=_dot22, gpu_op=gpu_dot22,
    cases=dict(
        test1=[rand(3, 4), rand(4, 5)],
        test2=[rand(1, 4), rand(4, 5)],
        test3=[rand(3, 1), rand(1, 5)],
        test4=[rand(3, 4), rand(4, 1)],
        # test5=[rand(0, 4), rand(4, 5)],
        # test6=[rand(3, 0), rand(0, 5)],
        # test7=[rand(3, 4), rand(4, 0)],
        # test8=[rand(0, 4), rand(4, 0)],
        # test9=[rand(0, 0), rand(0, 0)],
    )
)


def test_hgemm_swap():
    from theano.sandbox.cuda import nvcc_compiler
    if nvcc_compiler.nvcc_version < '7.5':
        raise SkipTest("SgemmEx is only avaialble on cuda 7.5+")

    v = tensor.vector(dtype='float16')
    m = tensor.matrix(dtype='float16')
    m2 = tensor.matrix(dtype='float16')
    m32 = tensor.matrix(dtype='float32')

    # test that we don't try to replace anything but matrix x matrix in float16
    f = theano.function([v, m], tensor.dot(v, m), mode=mode_with_gpu)
    assert len([node for node in f.maker.fgraph.apply_nodes
                if isinstance(node.op, GpuGemm)]) == 0

    f = theano.function([m32, m], tensor.dot(m32, m), mode=mode_with_gpu)
    assert len([node for node in f.maker.fgraph.apply_nodes
                if isinstance(node.op, GpuGemm)]) == 0

    f = theano.function([m, m2], tensor.dot(m, m2), mode=mode_with_gpu)
    assert len([node for node in f.maker.fgraph.apply_nodes
                if isinstance(node.op, GpuGemm)]) == 1

    v1 = numpy.random.random((3, 4)).astype('float16')
    v2 = numpy.random.random((4, 2)).astype('float16')

    of = f(v1, v2)
    on = numpy.dot(v1, v2)

    utt.assert_allclose(of, on)


def test_hgemm_alpha_output_merge():
    from theano.sandbox.cuda import nvcc_compiler
    if nvcc_compiler.nvcc_version < '7.5':
        raise SkipTest("SgemmEx is only avaialble on cuda 7.5+")

    m1 = tensor.matrix(dtype='float16')
    m2 = tensor.matrix(dtype='float16')

    b = tensor.matrix(dtype='float16')

    hgemm = numpy.asarray(0.05, dtype='float16') * (tensor.dot(m1, m2) + b)

    f = theano.function([m1, m2, b], hgemm, mode=mode_with_gpu)
    # there should be 3 gpu_from_host, 1 hgemm and 1 host_from_gpu
    assert len(f.maker.fgraph.apply_nodes) == 5
