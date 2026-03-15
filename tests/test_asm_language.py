"""Tests for assembly language (WLA-DX / generic) parsing."""

import pytest
from jcodemunch_mcp.parser import parse_file


# --- WLA-DX 65816 assembly (representative of SNES homebrew projects) ---

WLA_DX_SOURCE = """\
; Boot and initialization
.include "src/config/config.inc"

.define maxNumberOopObjs 48
.define oopStackTst $aa55
.def oopStackObj.length _sizeof_oopStackObj

.ENUM 0 export
OBJR_noErr db
OBJR_kill db
.ende

.STRUCT oopStackObj
flags       db
id          db
num         dw
properties  dw
dp          dw
init        dw
play        dw
kill        dw
.ENDST

.ramsection "global vars" bank 0 slot 2
  STACK_strt dw
  STACK_end dw
.ends

.section "oophandler"

;clear oop stack
core.object.init:
    php
    phd
    rep #$31
    lda #ZP
    tcd
    lda #0
    ldy #OopStackEnd-OopStack
    ldx #OopStack
    jsr ClearWRAM
    pld
    plp
    rts

;in:y=number of object to create, a:call parameter x:pointer
core.object.create:
    php
    phd
    rep #$31
    pha
    tdc
    pea ZP
    pld
    rts

.ends

.section "ScummVM dispatch" superfree

scummvm.executeOpcode:
    rep #$31
    jsr _fetchByte
    sta SCUMM.currentOpcode
    rts

_fetchByte:
    sep #$20
    lda [SCUMM.scriptPtr]
    rep #$20
    and #$00FF
    rts

.ends

.macro CLASS
.redefine __classid \\1
T_CLSS_\\@:
.db "\\1", 0
.endm

.macro METHOD
.redefine __method \\1
_\\1:
.endm

.macro SCRIPT
\\1:
.dw $BADE
.endm
"""


def test_parse_wla_dx_labels():
    """Test that global labels are extracted as functions."""
    symbols = parse_file(WLA_DX_SOURCE, "core/oop.65816", "asm")

    label_names = {s.name for s in symbols if s.kind == "function"}
    assert "core.object.init" in label_names
    assert "core.object.create" in label_names
    assert "scummvm.executeOpcode" in label_names


def test_parse_wla_dx_local_labels_excluded():
    """Test that _prefixed local labels are excluded."""
    symbols = parse_file(WLA_DX_SOURCE, "core/oop.65816", "asm")

    label_names = {s.name for s in symbols}
    assert "_fetchByte" not in label_names


def test_parse_wla_dx_sections():
    """Test that .section directives are extracted as class symbols."""
    symbols = parse_file(WLA_DX_SOURCE, "core/oop.65816", "asm")

    section_names = {s.name for s in symbols if s.kind == "class"}
    assert "oophandler" in section_names
    assert "ScummVM dispatch" in section_names


def test_parse_wla_dx_macros():
    """Test that .macro definitions are extracted as function symbols."""
    symbols = parse_file(WLA_DX_SOURCE, "core/oop.65816", "asm")

    macro_names = {s.name for s in symbols if s.kind == "function"}
    assert "CLASS" in macro_names
    assert "METHOD" in macro_names
    assert "SCRIPT" in macro_names


def test_parse_wla_dx_constants():
    """Test that .define/.def are extracted as constant symbols."""
    symbols = parse_file(WLA_DX_SOURCE, "core/oop.65816", "asm")

    const_names = {s.name for s in symbols if s.kind == "constant"}
    assert "maxNumberOopObjs" in const_names
    assert "oopStackTst" in const_names
    assert "oopStackObj.length" in const_names


def test_parse_wla_dx_structs():
    """Test that .struct definitions are extracted as type symbols."""
    symbols = parse_file(WLA_DX_SOURCE, "core/oop.65816", "asm")

    type_names = {s.name for s in symbols if s.kind == "type"}
    assert "oopStackObj" in type_names


def test_parse_wla_dx_enums():
    """Test that named .enum members are extracted."""
    symbols = parse_file(WLA_DX_SOURCE, "core/oop.65816", "asm")

    const_names = {s.name for s in symbols if s.kind == "constant"}
    assert "OBJR_noErr" in const_names
    assert "OBJR_kill" in const_names


def test_parse_wla_dx_ramsections():
    """Test that .ramsection directives are extracted."""
    symbols = parse_file(WLA_DX_SOURCE, "core/oop.65816", "asm")

    section_names = {s.name for s in symbols if s.kind == "class"}
    assert "global vars" in section_names


def test_parse_wla_dx_docstrings():
    """Test that preceding ;comments are captured as docstrings."""
    symbols = parse_file(WLA_DX_SOURCE, "core/oop.65816", "asm")

    init = next((s for s in symbols if s.name == "core.object.init"), None)
    assert init is not None
    assert "clear oop stack" in init.docstring

    create = next((s for s in symbols if s.name == "core.object.create"), None)
    assert create is not None
    assert "number of object to create" in create.docstring


def test_parse_wla_dx_qualified_names():
    """Test that labels inside sections get qualified names."""
    symbols = parse_file(WLA_DX_SOURCE, "core/oop.65816", "asm")

    init = next((s for s in symbols if s.name == "core.object.init"), None)
    assert init is not None
    # Should be qualified with the section name
    assert init.qualified_name == "oophandler::core.object.init"


def test_parse_wla_dx_language_field():
    """All symbols should have language='asm'."""
    symbols = parse_file(WLA_DX_SOURCE, "core/oop.65816", "asm")
    for s in symbols:
        assert s.language == "asm"


# --- Generic assembler (NASM/GAS style) ---

NASM_STYLE_SOURCE = """\
; Simple x86 NASM example
section .text
    global _start

_start:
    mov eax, 1
    mov ebx, 0
    int 0x80

print_hello:
    push ebp
    mov ebp, esp
    ret

%define BUFFER_SIZE 1024
%macro PROLOGUE 0
    push ebp
    mov ebp, esp
%endmacro

section .data
    msg db "Hello", 0
    len equ $ - msg

section .bss
    buffer resb 1024
"""


def test_parse_nasm_labels():
    """Test NASM-style label extraction."""
    symbols = parse_file(NASM_STYLE_SOURCE, "hello.asm", "asm")

    label_names = {s.name for s in symbols if s.kind == "function"}
    # _start is excluded because _ prefix = local label in WLA-DX convention.
    # This is a known trade-off for multi-dialect support.
    assert "print_hello" in label_names


def test_parse_nasm_defines():
    """Test NASM %define extraction."""
    symbols = parse_file(NASM_STYLE_SOURCE, "hello.asm", "asm")

    const_names = {s.name for s in symbols if s.kind == "constant"}
    assert "BUFFER_SIZE" in const_names


def test_parse_nasm_macros():
    """Test NASM %macro extraction."""
    symbols = parse_file(NASM_STYLE_SOURCE, "hello.asm", "asm")

    macro_names = {s.name for s in symbols if s.kind == "function"}
    assert "PROLOGUE" in macro_names


def test_parse_nasm_equ():
    """Test NASM equ constant extraction."""
    symbols = parse_file(NASM_STYLE_SOURCE, "hello.asm", "asm")

    const_names = {s.name for s in symbols if s.kind == "constant"}
    assert "len" in const_names


def test_parse_nasm_sections():
    """Test NASM section extraction."""
    symbols = parse_file(NASM_STYLE_SOURCE, "hello.asm", "asm")

    section_names = {s.name for s in symbols if s.kind == "class"}
    assert ".text" in section_names
    assert ".data" in section_names
    assert ".bss" in section_names


# --- GAS (GNU Assembler) style ---

GAS_STYLE_SOURCE = """\
    .text
    .globl main

main:
    pushq %rbp
    movq %rsp, %rbp
    ret

.type helper, @function
helper:
    ret

.set MAX_COUNT, 256
.equ STACK_SIZE, 4096

.macro save_regs
    pushq %rbx
    pushq %r12
.endm

.data
message:
    .asciz "Hello"

.bss
    .lcomm buffer, 1024
"""


def test_parse_gas_labels():
    """Test GAS-style label extraction."""
    symbols = parse_file(GAS_STYLE_SOURCE, "main.s", "asm")

    label_names = {s.name for s in symbols if s.kind == "function"}
    assert "main" in label_names
    assert "helper" in label_names


def test_parse_gas_constants():
    """Test GAS .set/.equ constant extraction."""
    symbols = parse_file(GAS_STYLE_SOURCE, "main.s", "asm")

    const_names = {s.name for s in symbols if s.kind == "constant"}
    assert "MAX_COUNT" in const_names
    assert "STACK_SIZE" in const_names


def test_parse_gas_macros():
    """Test GAS .macro extraction."""
    symbols = parse_file(GAS_STYLE_SOURCE, "main.s", "asm")

    macro_names = {s.name for s in symbols if s.kind == "function"}
    assert "save_regs" in macro_names


# --- CA65 (cc65 assembler for 6502) ---

CA65_STYLE_SOURCE = """\
.segment "CODE"

.proc main
    lda #$00
    sta $2000
    rts
.endproc

.proc irq_handler
    pha
    pla
    rti
.endproc

.define SCREEN_WIDTH 32
PPU_CTRL = $2000

.macro set_ppu_addr addr
    lda #.hibyte(addr)
    sta $2006
    lda #.lobyte(addr)
    sta $2006
.endmacro
"""


def test_parse_ca65_procs():
    """Test CA65 .proc extraction."""
    symbols = parse_file(CA65_STYLE_SOURCE, "main.s", "asm")

    func_names = {s.name for s in symbols if s.kind == "function"}
    assert "main" in func_names
    assert "irq_handler" in func_names


def test_parse_ca65_segments():
    """Test CA65 .segment extraction."""
    symbols = parse_file(CA65_STYLE_SOURCE, "main.s", "asm")

    section_names = {s.name for s in symbols if s.kind == "class"}
    assert "CODE" in section_names


def test_parse_ca65_constants():
    """Test CA65 constant extraction."""
    symbols = parse_file(CA65_STYLE_SOURCE, "main.s", "asm")

    const_names = {s.name for s in symbols if s.kind == "constant"}
    assert "SCREEN_WIDTH" in const_names
    assert "PPU_CTRL" in const_names


def test_parse_ca65_macros():
    """Test CA65 .macro/.endmacro extraction."""
    symbols = parse_file(CA65_STYLE_SOURCE, "main.s", "asm")

    macro_names = {s.name for s in symbols if s.kind == "function"}
    assert "set_ppu_addr" in macro_names


# --- Edge cases ---

def test_parse_empty_file():
    """Empty file should return no symbols."""
    symbols = parse_file("", "empty.asm", "asm")
    assert symbols == []


def test_parse_comments_only():
    """File with only comments should return no symbols."""
    source = "; This is a comment\n; Another comment\n"
    symbols = parse_file(source, "comments.asm", "asm")
    assert symbols == []


def test_extension_mapping():
    """Test that assembly file extensions are properly mapped."""
    from jcodemunch_mcp.parser.languages import LANGUAGE_EXTENSIONS

    assert LANGUAGE_EXTENSIONS.get(".asm") == "asm"
    assert LANGUAGE_EXTENSIONS.get(".s") == "asm"
    assert LANGUAGE_EXTENSIONS.get(".65816") == "asm"
    assert LANGUAGE_EXTENSIONS.get(".inc") == "asm"
    assert LANGUAGE_EXTENSIONS.get(".z80") == "asm"
    assert LANGUAGE_EXTENSIONS.get(".spc") == "asm"


def test_language_in_registry():
    """Test that asm is registered in LANGUAGE_REGISTRY."""
    from jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY

    assert "asm" in LANGUAGE_REGISTRY
