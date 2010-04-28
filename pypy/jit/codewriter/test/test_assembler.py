import struct
from pypy.jit.codewriter.assembler import Assembler
from pypy.jit.codewriter.flatten import SSARepr, Label, TLabel, Register
from pypy.jit.codewriter.flatten import ListOfKind
from pypy.jit.metainterp.history import AbstractDescr
from pypy.objspace.flow.model import Constant
from pypy.rpython.lltypesystem import lltype, llmemory


def test_assemble_simple():
    ssarepr = SSARepr("test")
    i0, i1, i2 = Register('int', 0), Register('int', 1), Register('int', 2)
    ssarepr.insns = [
        ('int_add', i0, i1, i2),
        ('int_return', i2),
        ]
    assembler = Assembler()
    jitcode = assembler.assemble(ssarepr)
    assert jitcode.code == ("\x00\x00\x01\x02"
                            "\x01\x02"
                            "\x00")
    assert assembler.insns == {'int_add/iii': 0,
                               'int_return/i': 1}

def test_assemble_consts():
    ssarepr = SSARepr("test")
    ssarepr.insns = [
        ('int_return', Register('int', 13)),
        ('int_return', Constant(18, lltype.Signed)),
        ('int_return', Constant(-4, lltype.Signed)),
        ('int_return', Constant(128, lltype.Signed)),
        ('int_return', Constant(-129, lltype.Signed)),
        ]
    assembler = Assembler()
    jitcode = assembler.assemble(ssarepr)
    assert jitcode.code == ("\x00\x0D"
                            "\x01\x12"   # use int_return/c for one-byte consts
                            "\x01\xFC"
                            "\x00\xFF"   # use int_return/i for larger consts
                            "\x00\xFE"
                            "\x00")      # highest_r_reg
    assert assembler.insns == {'int_return/i': 0,
                               'int_return/c': 1}
    assert jitcode.constants_i == [128, -129]

def test_assemble_float_consts():
    ssarepr = SSARepr("test")
    ssarepr.insns = [
        ('float_return', Register('float', 13)),
        ('float_return', Constant(18.0, lltype.Float)),
        ('float_return', Constant(-4.0, lltype.Float)),
        ('float_return', Constant(128.1, lltype.Float)),
        ]
    assembler = Assembler()
    jitcode = assembler.assemble(ssarepr)
    assert jitcode.code == ("\x00\x0D"
                            "\x00\xFF"
                            "\x00\xFE"
                            "\x00\xFD"
                            "\x00")
    assert assembler.insns == {'float_return/f': 0}
    assert jitcode.constants_f == [18.0, -4.0, 128.1]

def test_assemble_cast_consts():
    ssarepr = SSARepr("test")
    S = lltype.GcStruct('S')
    s = lltype.malloc(S)
    F = lltype.FuncType([], lltype.Signed)
    f = lltype.functionptr(F, 'f')
    ssarepr.insns = [
        ('int_return', Constant('X', lltype.Char)),
        ('int_return', Constant(unichr(0x1234), lltype.UniChar)),
        ('int_return', Constant(f, lltype.Ptr(F))),
        ('ref_return', Constant(s, lltype.Ptr(S))),
        ]
    assembler = Assembler()
    jitcode = assembler.assemble(ssarepr)
    assert jitcode.code == ("\x00\x58"
                            "\x01\xFF"
                            "\x01\xFE"
                            "\x02\xFF"
                            "\x00")
    assert assembler.insns == {'int_return/c': 0,
                               'int_return/i': 1,
                               'ref_return/r': 2}
    f_int = llmemory.cast_adr_to_int(llmemory.cast_ptr_to_adr(f))
    assert jitcode.constants_i == [0x1234, f_int]
    s_gcref = lltype.cast_opaque_ptr(llmemory.GCREF, s)
    assert jitcode.constants_r == [s_gcref]

def test_assemble_loop():
    ssarepr = SSARepr("test")
    i0, i1 = Register('int', 0x16), Register('int', 0x17)
    ssarepr.insns = [
        (Label('L1'),),
        ('goto_if_not_int_gt', TLabel('L2'), i0, Constant(4, lltype.Signed)),
        ('int_add', i1, i0, i1),
        ('int_sub', i0, Constant(1, lltype.Signed), i0),
        ('goto', TLabel('L1')),
        (Label('L2'),),
        ('int_return', i1),
        ]
    assembler = Assembler()
    jitcode = assembler.assemble(ssarepr)
    assert jitcode.code == ("\x00\x10\x00\x16\x04"
                            "\x01\x17\x16\x17"
                            "\x02\x16\x01\x16"
                            "\x03\x00\x00"
                            "\x04\x17"
                            "\x00")
    assert assembler.insns == {'goto_if_not_int_gt/Lic': 0,
                               'int_add/iii': 1,
                               'int_sub/ici': 2,
                               'goto/L': 3,
                               'int_return/i': 4}

def test_assemble_list():
    ssarepr = SSARepr("test")
    i0, i1 = Register('int', 0x16), Register('int', 0x17)
    ssarepr.insns = [
        ('foobar', ListOfKind('int', [i0, i1, Constant(42, lltype.Signed)]),
                   ListOfKind('ref', [])),
        ]
    assembler = Assembler()
    jitcode = assembler.assemble(ssarepr)
    assert jitcode._code() == "\x00\x03\x16\x17\xFF\x00"
    assert assembler.insns == {'foobar/IR': 0}
    assert jitcode.constants_i == [42]

def test_assemble_descr():
    class FooDescr(AbstractDescr):
        pass
    descrs = [FooDescr() for i in range(300)]
    ssarepr = SSARepr("test")
    ssarepr.insns = [('foobar', d) for d in descrs[::-1]]
    assembler = Assembler()
    jitcode = assembler.assemble(ssarepr)
    assert jitcode._code() == ''.join(["\x00" + struct.pack("<H", i)
                                       for i in range(300)])
    assert assembler.insns == {'foobar/d': 0}
    assert assembler.descrs == descrs[::-1]

def test_highest_r_reg():
    assembler = Assembler()
    ssarepr = SSARepr("test")
    ssarepr.insns = []
    jitcode = assembler.assemble(ssarepr)
    assert jitcode.highest_r_reg() == 0
    ssarepr = SSARepr("test")
    ssarepr.insns = [('foobar', Register('int', 51), Register('ref', 27))]
    jitcode = assembler.assemble(ssarepr)
    assert jitcode.highest_r_reg() == 27
