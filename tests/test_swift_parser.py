"""Tests for Swift and SwiftUI parsing."""

import pytest
from jcodemunch_mcp.parser import parse_file, LANGUAGE_REGISTRY

SWIFT_SOURCE = '''
struct MyStruct {
    var count: Int = 0
    func increment() { count += 1 }
}

class MyClass {
    let name: String
    init(name: String) { self.name = name }
}

enum MyEnum {
    case one, two
}

protocol MyProtocol {
    func doSomething()
}

extension MyStruct {
    func decrement() { count -= 1 }
}

typealias MyInt = Int

func globalFunc() {}
let GLOBAL_CONST = 42
'''

SWIFTUI_SOURCE = '''
import SwiftUI

struct MyView: View {
    @State private var count = 0

    var body: some View {
        VStack {
            Text("Count: \\(count)")
            Button("Increment") {
                count += 1
            }
        }
    }
}
'''

def test_parse_swift():
    """Test basic Swift construct extraction."""
    symbols = parse_file(SWIFT_SOURCE, "test.swift", "swift")

    # Check struct
    structs = [s for s in symbols if s.kind == "class" and s.name == "MyStruct"]
    assert len(structs) >= 1

    # Check class
    classes = [s for s in symbols if s.kind == "class" and s.name == "MyClass"]
    assert len(classes) == 1

    # Check enum
    enums = [s for s in symbols if s.kind == "class" and s.name == "MyEnum"]
    assert len(enums) == 1

    # Check protocol
    protocols = [s for s in symbols if s.kind == "type" and s.name == "MyProtocol"]
    assert len(protocols) == 1

    # Check extension
    extensions = [s for s in symbols if s.kind == "class" and s.name == "MyStruct" and s.line >= 20]
    assert len(extensions) == 1

    # Check typealias
    aliases = [s for s in symbols if s.kind == "type" and s.name == "MyInt"]
    assert len(aliases) == 1

    # Check global function
    funcs = [s for s in symbols if s.kind == "function" and s.name == "globalFunc"]
    assert len(funcs) == 1

    # Check global constant
    consts = [s for s in symbols if s.kind == "constant" and s.name == "GLOBAL_CONST"]
    assert len(consts) == 1

def test_parse_swiftui():
    """Test SwiftUI view and property wrapper extraction."""
    symbols = parse_file(SWIFTUI_SOURCE, "View.swift", "swift")

    # Check SwiftUI struct
    views = [s for s in symbols if s.kind == "class" and s.name == "MyView"]
    assert len(views) == 1

    # Check body property
    bodies = [s for s in symbols if s.name == "body" and s.parent == views[0].id]
    assert len(bodies) == 1

    # Check @State property (currently extracted as generic property/constant)
    states = [s for s in symbols if s.name == "count" and s.parent == views[0].id]
    assert len(states) == 1
