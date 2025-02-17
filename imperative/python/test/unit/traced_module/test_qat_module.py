import io
from functools import partial
from itertools import chain
from typing import Callable

import numpy as np

import megengine as mge
import megengine.functional as F
import megengine.module as M
import megengine.quantization as Q
from megengine import Tensor
from megengine.module.qat.module import QATModule
from megengine.traced_module import TracedModule, trace_module


def get_subattr(self: M.Module, name: str):
    if name == "":
        return self
    module_path, _, name = name.rpartition(".")
    if module_path == "":
        return getattr(self, name)
    module_names = module_path.split(".")
    for item in module_names:
        self = getattr(self, item)
        if not isinstance(self, M.Module):
            raise AttributeError("`{}` is not an Module".format(item))
    return getattr(self, name)


class Myblcok(M.Module):
    def __init__(self,):
        super().__init__()
        self.conv0 = M.ConvBnRelu2d(3, 3, 3, 1, 1)
        self.conv1 = M.ConvBn2d(3, 3, 1, 1, 0)
        self.conv2 = M.ConvBn2d(3, 3, 1, 1, 0)
        self.add = M.Elemwise("FUSE_ADD_RELU")

    def forward(self, x):
        x = self.conv0(x)
        x0 = self.conv1(x)
        x1 = self.conv2(x)
        o = self.add(x0, x1)
        return o


class MyModule(M.Module):
    def __init__(self):
        super().__init__()
        self.block0 = Myblcok()
        self.block1 = Myblcok()

    def forward(self, x):
        x = self.block0(x)
        x = self.block1(x)
        return x


class MyMinMaxObserver(Q.MinMaxObserver):
    pass


class MyTQT(Q.TQT):
    pass


def get_lsq_config(lsq_cls):
    return Q.QConfig(
        weight_observer=None,
        act_observer=None,
        weight_fake_quant=partial(lsq_cls, dtype="qint8_narrow"),
        act_fake_quant=partial(lsq_cls, dtype="qint8"),
    )


def get_observer_config(observer_cls):
    return Q.QConfig(
        weight_observer=partial(observer_cls, dtype="qint8_narrow"),
        act_observer=partial(observer_cls, dtype="qint8"),
        weight_fake_quant=None,
        act_fake_quant=None,
    )


def get_qparams(mod: QATModule):
    weight_qparams, act_qparams = None, None
    if mod.act_observer is not None:
        act_qparams = mod.act_observer.get_qparams()
    if mod.act_fake_quant:
        act_qparams = mod.act_fake_quant.get_qparams()

    if mod.weight_observer is not None:
        weight_qparams = mod.weight_observer.get_qparams()
    if mod.weight_fake_quant:
        weight_qparams = mod.weight_fake_quant.get_qparams()

    return weight_qparams, act_qparams


def check_qparams(qparmsa: Q.QParams, qparmsb: Q.QParams):
    assert qparmsa.dtype_meta == qparmsb.dtype_meta
    assert qparmsa.mode == qparmsb.mode
    np.testing.assert_equal(qparmsa.scale.numpy(), qparmsb.scale.numpy())
    if qparmsa.zero_point is not None:
        np.testing.assert_equal(qparmsa.zero_point.numpy(), qparmsb.zero_point.numpy())


def build_observered_net(net: M.Module, observer_cls):
    qat_net = Q.quantize_qat(net, qconfig=get_observer_config(observer_cls))
    Q.enable_observer(qat_net)
    inp = Tensor(np.random.random(size=(5, 3, 32, 32)))
    qat_net(inp)
    Q.disable_observer(qat_net)
    return qat_net


def build_fakequanted_net(net: QATModule, fakequant_cls):
    qat_net = Q.reset_qconfig(net, get_lsq_config(fakequant_cls))
    return qat_net


def test_trace_qat():
    def _check_qat_module(qat_net: QATModule):
        inp = Tensor(np.random.random(size=(5, 3, 32, 32)))
        traced_net = trace_module(qat_net, inp)

        for name, qat_module in qat_net.named_modules():
            if not isinstance(qat_module, QATModule):
                continue
            traced_qat_module = get_subattr(traced_net, name)
            weight_qparams, act_qparams = get_qparams(qat_module)
            traced_weight_qparams, traced_act_qparams = get_qparams(traced_qat_module)
            if weight_qparams:
                check_qparams(weight_qparams, traced_weight_qparams)
            if act_qparams:
                check_qparams(act_qparams, traced_act_qparams)

    _check_qat_module(build_observered_net(MyModule(), Q.MinMaxObserver))
    _check_qat_module(build_observered_net(MyModule(), MyMinMaxObserver))
    _check_qat_module(
        build_fakequanted_net(build_observered_net(MyModule(), Q.MinMaxObserver), Q.TQT)
    )
    _check_qat_module(
        build_fakequanted_net(build_observered_net(MyModule(), Q.MinMaxObserver), MyTQT)
    )


def test_load_param():
    def _check_param(moda: M.Module, modb: M.Module):
        for name, attr in chain(moda.named_parameters(), moda.named_buffers()):
            traced_attr = get_subattr(modb, name)
            np.testing.assert_equal(attr.numpy(), traced_attr.numpy())

    def _check_module(build_func: Callable):
        net = build_func()
        buffer = io.BytesIO()
        mge.save(net.state_dict(), buffer)
        buffer.seek(0)

        inp = Tensor(np.random.random(size=(5, 3, 32, 32)))
        traced_net = trace_module(build_func(), inp)
        traced_net.load_state_dict(mge.load(buffer))

        _check_param(net, traced_net)

        buffer.seek(0)
        traced_net = trace_module(build_func(), inp).flatten()
        traced_net.load_state_dict(mge.load(buffer))

        _check_param(net, traced_net)

    _check_module(lambda: MyModule())
    _check_module(lambda: build_observered_net(MyModule(), Q.MinMaxObserver))


def test_qualname():
    def _check_qualname(net):
        inp = Tensor(np.random.random(size=(5, 3, 32, 32)))
        traced_net = trace_module(net, inp)
        base_qualname = traced_net.graph.qualname
        for node in traced_net.graph.nodes():
            qualname = node.qualname
            qualname = qualname[len(base_qualname) + 1 :]
            if qualname.endswith("]"):
                qualname = qualname.rsplit(".", 1)[0]
            if qualname.startswith("["):
                qualname = ""
            traced_attr = get_subattr(traced_net, qualname)
            orig_attr = get_subattr(net, qualname)
            assert traced_attr is not None
            assert orig_attr is not None

    _check_qualname(MyModule())
    _check_qualname(build_observered_net(MyModule(), Q.MinMaxObserver))
