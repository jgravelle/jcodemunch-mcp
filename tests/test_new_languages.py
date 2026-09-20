"""Tests for the 10 new language parsers added in v1.42.0.

Languages: Pascal, MATLAB, Ada, COBOL, Common Lisp, Solidity, Zig,
PowerShell, Apex, OCaml.  Plus PL/SQL extension mapping.
"""

import pytest
from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.languages import get_language_for_path, LANGUAGE_EXTENSIONS


# ── extension mapping ──────────────────────────────────────────────────────

@pytest.mark.parametrize("ext,lang", [
    (".pas", "pascal"), (".dpr", "pascal"), (".dpk", "pascal"), (".lpr", "pascal"), (".pp", "pascal"),
    (".mat", "matlab"), (".mlx", "matlab"),
    (".adb", "ada"), (".ads", "ada"),
    (".cob", "cobol"), (".cbl", "cobol"), (".cpy", "cobol"),
    (".lisp", "commonlisp"), (".cl", "commonlisp"), (".lsp", "commonlisp"), (".asd", "commonlisp"),
    (".sol", "solidity"),
    (".zig", "zig"), (".zon", "zig"),
    (".ps1", "powershell"), (".psm1", "powershell"), (".psd1", "powershell"),
    (".cls", "apex"), (".trigger", "apex"),
    (".ml", "ocaml"), (".mli", "ocaml"),
    (".pls", "sql"), (".plb", "sql"), (".pck", "sql"), (".pkb", "sql"), (".pks", "sql"),
])
def test_extension_mapping(ext, lang):
    assert LANGUAGE_EXTENSIONS[ext] == lang


# ── MATLAB vs Objective-C .m disambiguation ────────────────────────────────

def test_m_file_defaults_to_objc():
    """Without MATLAB path markers, .m files default to Objective-C."""
    assert get_language_for_path("src/AppDelegate.m") == "objc"


def test_m_file_matlab_path():
    """With MATLAB path markers, .m files map to MATLAB."""
    assert get_language_for_path("matlab/signal_processing.m") == "matlab"
    assert get_language_for_path("toolbox/utils.m") == "matlab"


# ── Pascal ──────────────────────────────────────────────────────────────────

PASCAL_CODE = """\
program HelloWorld;

type
  TMyClass = class
    procedure DoSomething(x: Integer);
    function Calculate(a, b: Integer): Integer;
  end;

procedure GlobalProc(x: Integer);
begin
  WriteLn(x);
end;

function GlobalFunc(a: Integer): Integer;
begin
  Result := a * 2;
end;

const
  MAX_SIZE = 100;
"""


def test_pascal_parsing():
    symbols = parse_file(PASCAL_CODE, "test.pas", "pascal")
    names = {s.name for s in symbols}
    assert "TMyClass" in names
    assert "GlobalProc" in names
    assert "GlobalFunc" in names
    assert "MAX_SIZE" in names
    cls = [s for s in symbols if s.name == "TMyClass"][0]
    assert cls.kind == "class"
    proc = [s for s in symbols if s.name == "GlobalProc"][0]
    assert proc.kind == "function"
    assert "procedure" in proc.signature.lower()


# ── MATLAB ──────────────────────────────────────────────────────────────────

MATLAB_CODE = """\
function result = myFunction(a, b)
    result = a + b;
end

classdef MyClass < BaseClass
    methods
        function obj = MyClass(val)
            obj.Value = val;
        end
        function result = calculate(obj, x)
            result = obj.Value + x;
        end
    end
end
"""


def test_matlab_parsing():
    symbols = parse_file(MATLAB_CODE, "test.m", "matlab")
    names = {s.name for s in symbols}
    assert "myFunction" in names
    assert "MyClass" in names
    func = [s for s in symbols if s.name == "myFunction"][0]
    assert func.kind == "function"
    assert "result" in func.signature
    cls = [s for s in symbols if s.name == "MyClass"][0]
    assert cls.kind == "class"
    # Methods inside class
    methods = [s for s in symbols if s.kind == "method"]
    assert len(methods) >= 1


# ── Ada ─────────────────────────────────────────────────────────────────────

ADA_CODE = """\
package body Math is
   function Add(X, Y : Integer) return Integer is
   begin
      return X + Y;
   end Add;

   procedure Print_Result(Value : Integer) is
   begin
      null;
   end Print_Result;

   type Color is (Red, Green, Blue);

   Max_Size : constant Integer := 100;
end Math;
"""


def test_ada_parsing():
    symbols = parse_file(ADA_CODE, "math.adb", "ada")
    names = {s.name for s in symbols}
    assert "Math" in names
    assert "Add" in names
    assert "Print_Result" in names
    assert "Color" in names
    assert "Max_Size" in names
    pkg = [s for s in symbols if s.name == "Math"][0]
    assert pkg.kind == "class"
    add = [s for s in symbols if s.name == "Add"][0]
    assert add.kind == "function"
    assert "Math::" in add.qualified_name
    color = [s for s in symbols if s.name == "Color"][0]
    assert color.kind == "type"
    const = [s for s in symbols if s.name == "Max_Size"][0]
    assert const.kind == "constant"


# ── COBOL ───────────────────────────────────────────────────────────────────

COBOL_CODE = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. HELLO-WORLD.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-NAME PIC X(30) VALUE 'World'.
       01  WS-COUNT PIC 9(3) VALUE 0.
       PROCEDURE DIVISION.
       MAIN-PARA.
           DISPLAY 'Hello ' WS-NAME.
           PERFORM CALC-PARA.
           STOP RUN.
       CALC-PARA.
           ADD 1 TO WS-COUNT.
"""


def test_cobol_parsing():
    symbols = parse_file(COBOL_CODE, "hello.cob", "cobol")
    names = {s.name for s in symbols}
    assert "HELLO-WORLD" in names
    assert "MAIN-PARA" in names
    assert "CALC-PARA" in names
    assert "WS-NAME" in names
    assert "WS-COUNT" in names
    prog = [s for s in symbols if s.name == "HELLO-WORLD"][0]
    assert prog.kind == "class"
    para = [s for s in symbols if s.name == "MAIN-PARA"][0]
    assert para.kind == "function"
    data = [s for s in symbols if s.name == "WS-NAME"][0]
    assert data.kind == "constant"


# ── Common Lisp ────────────────────────────────────────────────────────────

COMMONLISP_CODE = """\
(defun add (x y)
  "Add two numbers."
  (+ x y))

(defclass point ()
  ((x :initarg :x :accessor point-x)
   (y :initarg :y :accessor point-y)))

(defmethod area ((p point))
  (* (point-x p) (point-y p)))

(defmacro when-let ((var expr) &body body)
  `(let ((,var ,expr))
     (when ,var ,@body)))

(defvar *max-size* 100)
(defconstant +pi+ 3.14159)

(defstruct person
  name
  age)
"""


def test_commonlisp_parsing():
    symbols = parse_file(COMMONLISP_CODE, "test.lisp", "commonlisp")
    names = {s.name for s in symbols}
    assert "add" in names
    assert "area" in names
    assert "when-let" in names
    assert "point" in names
    assert "*max-size*" in names
    assert "+pi+" in names
    assert "person" in names
    add = [s for s in symbols if s.name == "add"][0]
    assert add.kind == "function"
    point = [s for s in symbols if s.name == "point"][0]
    assert point.kind == "class"
    maxsz = [s for s in symbols if s.name == "*max-size*"][0]
    assert maxsz.kind == "constant"
    person = [s for s in symbols if s.name == "person"][0]
    assert person.kind == "class"


# ── Solidity ────────────────────────────────────────────────────────────────

SOLIDITY_CODE = """\
pragma solidity ^0.8.0;

contract MyContract {
    uint256 public value;

    event ValueChanged(uint256 newValue);

    modifier onlyOwner() {
        require(msg.sender == owner);
        _;
    }

    function setValue(uint256 _value) public onlyOwner {
        value = _value;
    }

    function getValue() public view returns (uint256) {
        return value;
    }

    struct Point { uint256 x; uint256 y; }
    enum Status { Active, Inactive }
}

interface IToken {
    function transfer(address to, uint256 amount) external returns (bool);
}

library SafeMath {
    function add(uint256 a, uint256 b) internal pure returns (uint256) {
        return a + b;
    }
}
"""


def test_solidity_parsing():
    symbols = parse_file(SOLIDITY_CODE, "test.sol", "solidity")
    names = {s.name for s in symbols}
    assert "MyContract" in names
    assert "setValue" in names
    assert "getValue" in names
    assert "ValueChanged" in names
    assert "onlyOwner" in names
    assert "Point" in names
    assert "Status" in names
    assert "value" in names
    assert "IToken" in names
    assert "SafeMath" in names
    assert "transfer" in names
    assert "add" in names
    contract = [s for s in symbols if s.name == "MyContract"][0]
    assert contract.kind == "class"
    iface = [s for s in symbols if s.name == "IToken"][0]
    assert iface.kind == "type"
    func = [s for s in symbols if s.name == "setValue"][0]
    # #788: this asserted `function` for a member of `MyContract`, which is the
    # defect -- Solidity has had free functions since 0.7.0, so the kind is
    # decided by whether the declaration has an owner.
    assert func.kind == "method"
    assert "MyContract" in func.qualified_name
    assert func.parent == [s for s in symbols if s.name == "MyContract"][0].id


# ── Zig ─────────────────────────────────────────────────────────────────────

ZIG_CODE = """\
const std = @import("std");

pub fn add(a: i32, b: i32) i32 {
    return a + b;
}

fn privateFunc() void {}

const MyStruct = struct {
    x: i32,
    y: i32,

    pub fn init(x: i32, y: i32) MyStruct {
        return .{ .x = x, .y = y };
    }
};

const MyEnum = enum {
    foo,
    bar,
};

const MAX_SIZE: usize = 100;

test "add test" {
    try std.testing.expectEqual(add(1, 2), 3);
}
"""


def test_zig_parsing():
    symbols = parse_file(ZIG_CODE, "test.zig", "zig")
    names = {s.name for s in symbols}
    assert "add" in names
    assert "privateFunc" in names
    assert "MyStruct" in names
    assert "MyEnum" in names
    assert "MAX_SIZE" in names
    assert "std" in names
    add = [s for s in symbols if s.name == "add"][0]
    assert add.kind == "function"
    st = [s for s in symbols if s.name == "MyStruct"][0]
    assert st.kind == "class"
    en = [s for s in symbols if s.name == "MyEnum"][0]
    assert en.kind == "type"
    const = [s for s in symbols if s.name == "MAX_SIZE"][0]
    assert const.kind == "constant"
    # Test declaration
    tests = [s for s in symbols if "test" in s.name.lower() and s.name != "std"]
    assert len(tests) >= 1


# ── PowerShell ──────────────────────────────────────────────────────────────

POWERSHELL_CODE = """\
function Get-UserInfo {
    param(
        [string]$Name,
        [int]$Age
    )
    Write-Host "Name: $Name, Age: $Age"
}

function Set-Config([string]$Key, [string]$Value) {
    $config[$Key] = $Value
}

class MyClass {
    [string]$Name

    [string] GetName() {
        return $this.Name
    }

    [void] SetName([string]$name) {
        $this.Name = $name
    }
}

enum Color {
    Red
    Green
    Blue
}
"""


def test_powershell_parsing():
    symbols = parse_file(POWERSHELL_CODE, "test.ps1", "powershell")
    names = {s.name for s in symbols}
    assert "Get-UserInfo" in names
    assert "Set-Config" in names
    assert "MyClass" in names
    assert "Color" in names
    func = [s for s in symbols if s.name == "Get-UserInfo"][0]
    assert func.kind == "function"
    cls = [s for s in symbols if s.name == "MyClass"][0]
    assert cls.kind == "class"
    enum = [s for s in symbols if s.name == "Color"][0]
    assert enum.kind == "type"
    # Class methods
    methods = [s for s in symbols if s.kind == "method"]
    method_names = {m.name for m in methods}
    assert "GetName" in method_names
    assert "SetName" in method_names


# ── Apex ────────────────────────────────────────────────────────────────────

APEX_CODE = """\
public class AccountController {
    public static List<Account> getAccounts() {
        return [SELECT Id, Name FROM Account];
    }

    public void updateAccount(Account acc) {
        update acc;
    }

    public interface IProcessor {
        void process(SObject record);
    }

    public enum Status {
        ACTIVE,
        INACTIVE
    }
}

trigger AccountTrigger on Account (before insert, before update) {
    for (Account acc : Trigger.new) {
        acc.Description = 'Updated';
    }
}
"""


def test_apex_parsing():
    symbols = parse_file(APEX_CODE, "AccountController.cls", "apex")
    names = {s.name for s in symbols}
    assert "AccountController" in names
    assert "getAccounts" in names
    assert "updateAccount" in names
    assert "IProcessor" in names
    assert "Status" in names
    assert "AccountTrigger" in names
    cls = [s for s in symbols if s.name == "AccountController"][0]
    assert cls.kind == "class"
    method = [s for s in symbols if s.name == "getAccounts"][0]
    assert method.kind == "method"
    assert "AccountController" in method.qualified_name
    trigger = [s for s in symbols if s.name == "AccountTrigger"][0]
    assert trigger.kind == "function"


# ── OCaml ───────────────────────────────────────────────────────────────────

OCAML_CODE = """\
let add x y = x + y

let rec factorial n =
  if n <= 1 then 1
  else n * factorial (n - 1)

type color = Red | Green | Blue

type point = {
  x : float;
  y : float;
}

module MyModule = struct
  let helper x = x + 1
end

class my_class x_init = object
  val mutable x = x_init
  method get_x = x
end

let pi = 3.14159
"""


def test_ocaml_parsing():
    symbols = parse_file(OCAML_CODE, "test.ml", "ocaml")
    names = {s.name for s in symbols}
    assert "add" in names
    assert "factorial" in names
    assert "color" in names
    assert "point" in names
    assert "MyModule" in names
    assert "my_class" in names
    assert "pi" in names
    add = [s for s in symbols if s.name == "add"][0]
    assert add.kind == "function"
    color = [s for s in symbols if s.name == "color"][0]
    assert color.kind == "type"
    module = [s for s in symbols if s.name == "MyModule"][0]
    assert module.kind == "class"
    cls = [s for s in symbols if s.name == "my_class"][0]
    assert cls.kind == "class"
    pi = [s for s in symbols if s.name == "pi"][0]
    assert pi.kind == "constant"
    # Nested module function
    helper = [s for s in symbols if s.name == "helper"]
    assert len(helper) == 1
    assert "MyModule" in helper[0].qualified_name
