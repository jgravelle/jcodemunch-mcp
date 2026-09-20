"""Tests for the 6 new language parsers added in v1.43.0.

Languages: F#, Clojure, Emacs Lisp, Nim, Tcl, D.
"""

import pytest
from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.languages import get_language_for_path, LANGUAGE_EXTENSIONS


# ── extension mapping ──────────────────────────────────────────────────────

@pytest.mark.parametrize("ext,lang", [
    (".fs", "fsharp"), (".fsi", "fsharp"), (".fsx", "fsharp"),
    (".clj", "clojure"), (".cljs", "clojure"), (".cljc", "clojure"), (".edn", "clojure"),
    (".el", "elisp"),
    (".nim", "nim"), (".nims", "nim"), (".nimble", "nim"),
    (".tcl", "tcl"), (".tk", "tcl"), (".itcl", "tcl"),
    (".d", "dlang"), (".di", "dlang"),
])
def test_extension_mapping(ext, lang):
    assert LANGUAGE_EXTENSIONS[ext] == lang


# ── F# ────────────────────────────────────────────────────────────────────

FSHARP_CODE = """\
module MyModule =
    let add x y = x + y
    let greet (name: string) : string = sprintf "Hello %s" name
    type Person = { Name: string; Age: int }
    type Shape =
        | Circle of float
        | Rectangle of float * float
    let pi = 3.14
"""


def test_fsharp_parsing():
    syms = parse_file(FSHARP_CODE, "test.fs", "fsharp")
    names = {s.name: s for s in syms}
    assert "MyModule" in names
    assert names["MyModule"].kind == "class"
    assert "add" in names
    assert names["add"].kind == "function"
    assert names["add"].qualified_name == "MyModule.add"
    assert "greet" in names
    assert names["greet"].kind == "function"
    assert "Person" in names
    assert names["Person"].kind == "type"
    assert names["Person"].qualified_name == "MyModule.Person"
    assert "Shape" in names
    assert names["Shape"].kind == "type"
    assert "pi" in names
    assert names["pi"].kind == "constant"
    assert names["pi"].qualified_name == "MyModule.pi"


FSHARP_TOPLEVEL = """\
let topFunc x = x + 1
type TopType = { Value: int }
"""


def test_fsharp_toplevel():
    syms = parse_file(FSHARP_TOPLEVEL, "top.fs", "fsharp")
    names = {s.name: s for s in syms}
    assert "topFunc" in names
    assert names["topFunc"].kind == "function"
    assert names["topFunc"].qualified_name == "topFunc"
    assert "TopType" in names
    assert names["TopType"].kind == "type"


# ── Clojure ───────────────────────────────────────────────────────────────

CLOJURE_CODE = """\
(ns myapp.core
  (:require [clojure.string :as str]))

(defn greet [name]
  (str "Hello " name))

(def pi 3.14159)

(defmacro unless [pred & body]
  `(when (not ~pred) ~@body))

(defprotocol Greeter
  (say-hello [this]))

(defrecord Person [name age])

(defmulti area :shape)
"""


def test_clojure_parsing():
    syms = parse_file(CLOJURE_CODE, "core.clj", "clojure")
    names = {s.name: s for s in syms}
    assert "greet" in names
    assert names["greet"].kind == "function"
    assert names["greet"].qualified_name == "myapp.core/greet"
    assert "[name]" in names["greet"].signature
    assert "pi" in names
    assert names["pi"].kind == "constant"
    assert names["pi"].qualified_name == "myapp.core/pi"
    assert "unless" in names
    assert names["unless"].kind == "function"
    assert "Greeter" in names
    assert names["Greeter"].kind == "type"
    assert "Person" in names
    assert names["Person"].kind == "type"
    assert "area" in names
    assert names["area"].kind == "function"


CLOJURE_NO_NS = """\
(defn helper [x] (+ x 1))
"""


def test_clojure_no_namespace():
    syms = parse_file(CLOJURE_NO_NS, "helper.clj", "clojure")
    assert len(syms) == 1
    assert syms[0].name == "helper"
    assert syms[0].qualified_name == "helper"


# ── Emacs Lisp ────────────────────────────────────────────────────────────

ELISP_CODE = """\
(defun greet (name)
  "Greet someone by NAME."
  (message "Hello %s" name))

(defvar my-counter 0 "A counter variable.")

(defconst pi 3.14159 "Pi constant.")

(defmacro unless (pred &rest body)
  `(when (not ,pred) ,@body))
"""


def test_elisp_parsing():
    syms = parse_file(ELISP_CODE, "init.el", "elisp")
    names = {s.name: s for s in syms}
    assert "greet" in names
    assert names["greet"].kind == "function"
    assert "(name)" in names["greet"].signature
    assert names["greet"].docstring == "Greet someone by NAME."
    assert "my-counter" in names
    assert names["my-counter"].kind == "constant"
    assert names["my-counter"].docstring == "A counter variable."
    assert "pi" in names
    assert names["pi"].kind == "constant"
    assert "unless" in names
    assert names["unless"].kind == "function"
    assert "defmacro" in names["unless"].signature


# ── Nim ───────────────────────────────────────────────────────────────────

NIM_CODE = """\
proc add(x, y: int): int =
  result = x + y

func multiply(x, y: float): float =
  x * y

type
  Person = object
    name: string
    age: int

  Shape = enum
    Circle
    Rectangle

var counter: int = 0
let pi = 3.14159
const maxSize = 100

template log(msg: string) =
  echo msg

macro genCode(body: untyped): untyped =
  result = body
"""


def test_nim_parsing():
    syms = parse_file(NIM_CODE, "main.nim", "nim")
    names = {s.name: s for s in syms}
    assert "add" in names
    assert names["add"].kind == "function"
    assert "proc" in names["add"].signature
    assert "(x, y: int)" in names["add"].signature
    assert "multiply" in names
    assert names["multiply"].kind == "function"
    assert "func" in names["multiply"].signature
    assert "Person" in names
    assert names["Person"].kind == "type"
    assert "Shape" in names
    assert names["Shape"].kind == "type"
    assert "counter" in names
    assert names["counter"].kind == "constant"
    assert "var" in names["counter"].signature
    assert "pi" in names
    assert names["pi"].kind == "constant"
    assert "maxSize" in names
    assert names["maxSize"].kind == "constant"
    assert "log" in names
    assert names["log"].kind == "function"
    assert "template" in names["log"].signature
    assert "genCode" in names
    assert names["genCode"].kind == "function"
    assert "macro" in names["genCode"].signature


# ── Tcl ───────────────────────────────────────────────────────────────────

TCL_CODE = """\
proc greet {name} {
    puts "Hello $name"
}

proc add {x y} {
    expr {$x + $y}
}

namespace eval MyNS {
    proc helper {x y} {
        expr {$x + $y}
    }
}
"""


def test_tcl_parsing():
    syms = parse_file(TCL_CODE, "app.tcl", "tcl")
    names = {s.name: s for s in syms}
    assert "greet" in names
    assert names["greet"].kind == "function"
    assert "{name}" in names["greet"].signature
    assert "add" in names
    assert names["add"].kind == "function"
    assert "MyNS" in names
    assert names["MyNS"].kind == "class"
    assert "helper" in names
    assert names["helper"].kind == "function"
    assert names["helper"].qualified_name == "MyNS::helper"


TCL_NESTED_NS = """\
namespace eval Outer {
    namespace eval Inner {
        proc deep {} {
            puts "deep"
        }
    }
}
"""


def test_tcl_nested_namespace():
    syms = parse_file(TCL_NESTED_NS, "nested.tcl", "tcl")
    names = {s.name: s for s in syms}
    assert "Outer" in names
    assert "Inner" in names
    assert names["Inner"].qualified_name == "Outer::Inner"
    assert "deep" in names
    assert names["deep"].qualified_name == "Outer::Inner::deep"


# ── D ─────────────────────────────────────────────────────────────────────

DLANG_CODE = """\
module mymodule;

int add(int x, int y) {
    return x + y;
}

class Person {
    string name;
    int age;
    this(string name, int age) {
        this.name = name;
        this.age = age;
    }
    string greet() {
        return "Hello " ~ name;
    }
}

struct Point {
    float x, y;
}

interface Drawable {
    void draw();
}

enum Color { Red, Green, Blue }

template Max(T) {
    T max(T a, T b) { return a > b ? a : b; }
}
"""


def test_dlang_parsing():
    syms = parse_file(DLANG_CODE, "app.d", "dlang")
    names = {s.name: s for s in syms}
    assert "add" in names
    assert names["add"].kind == "function"
    assert "int add(int x, int y)" in names["add"].signature
    assert "Person" in names
    assert names["Person"].kind == "class"
    assert "greet" in names
    # #776: this asserted `function` for a member of `Person`, which is the
    # defect -- D spells a free function and a method with the same node type,
    # so the owner is the only thing that separates them. `add` above is the
    # control and stays a `function`.
    assert names["greet"].kind == "method"
    assert names["greet"].qualified_name == "Person.greet"
    assert names["greet"].parent == names["Person"].id
    assert "Point" in names
    assert names["Point"].kind == "class"
    assert "struct" in names["Point"].signature
    assert "Drawable" in names
    assert names["Drawable"].kind == "class"
    assert "interface" in names["Drawable"].signature
    assert "Color" in names
    assert names["Color"].kind == "type"
    assert "enum" in names["Color"].signature
    assert "Max" in names
    assert names["Max"].kind == "function"
    assert "template" in names["Max"].signature


DLANG_NESTED = """\
class Outer {
    int method1() { return 1; }
    class Inner {
        void innerMethod() {}
    }
}
"""


def test_dlang_nested_classes():
    syms = parse_file(DLANG_NESTED, "nested.d", "dlang")
    names = {s.name: s for s in syms}
    assert "Outer" in names
    assert "method1" in names
    assert names["method1"].qualified_name == "Outer.method1"
    assert "Inner" in names
    assert names["Inner"].qualified_name == "Outer.Inner"
    assert "innerMethod" in names
    assert names["innerMethod"].qualified_name == "Outer.Inner.innerMethod"
