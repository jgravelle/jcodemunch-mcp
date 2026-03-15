"""Tests for language-specific parsing."""

import pytest
from jcodemunch_mcp.parser import parse_file


JAVASCRIPT_SOURCE = '''
/** Greet a user. */
function greet(name) {
    return `Hello, ${name}!`;
}

class Calculator {
    /** Add two numbers. */
    add(a, b) {
        return a + b;
    }
}

const MAX_RETRY = 5;
'''


def test_parse_javascript():
    """Test JavaScript parsing."""
    symbols = parse_file(JAVASCRIPT_SOURCE, "app.js", "javascript")
    
    # Should have function, class, method, constant
    func = next((s for s in symbols if s.name == "greet"), None)
    assert func is not None
    assert func.kind == "function"
    assert "Greet a user" in func.docstring
    
    cls = next((s for s in symbols if s.name == "Calculator"), None)
    assert cls is not None
    assert cls.kind == "class"
    
    method = next((s for s in symbols if s.name == "add"), None)
    assert method is not None
    assert method.kind == "method"


TYPESCRIPT_SOURCE = '''
interface User {
    name: string;
}

/** Get user by ID. */
function getUser(id: number): User {
    return { name: "Test" };
}

class UserService {
    private users: User[] = [];
    
    @cache()
    findById(id: number): User | undefined {
        return this.users.find(u => u.id === id);
    }
}

type ID = string | number;
'''


def test_parse_typescript():
    """Test TypeScript parsing."""
    symbols = parse_file(TYPESCRIPT_SOURCE, "service.ts", "typescript")
    
    # Should have interface, function, class, method, type alias
    func = next((s for s in symbols if s.name == "getUser"), None)
    assert func is not None
    assert func.kind == "function"
    
    interface = next((s for s in symbols if s.name == "User"), None)
    assert interface is not None
    assert interface.kind == "type"



TSX_SOURCE = '''
interface User {
    name: string;
}

/** Get user by ID. */
function getUser(id: number): User {
    return { name: "Test" };
}

class UserService {
    private users: User[] = [];

    findById(id: number): User | undefined {
        return this.users.find(u => u.id === id);
    }
}

type ID = string | number;

export function UserList() {
  return (
    <ul>
      {UserService.getUsers().map((user) => (
          <li>{user.name}</li>
      ))}
    </ul>
  )
}
'''

def test_parse_tsx():
    """Test TSX parsing (TypeScript with JSX)."""
    symbols = parse_file(TSX_SOURCE, "service.tsx", "tsx")

    symbol = next((s for s in symbols if s.name == "User"), None)
    assert symbol is not None
    assert symbol.kind == "type"

    symbol = next((s for s in symbols if s.name == "getUser"), None)
    assert symbol is not None
    assert symbol.kind == "function"

    symbol = next((s for s in symbols if s.name == "UserService"), None)
    assert symbol is not None
    assert symbol.kind == "class"

    symbol = next((s for s in symbols if s.name == "findById"), None)
    assert symbol is not None
    assert symbol.kind == "method"

    symbol = next((s for s in symbols if s.name == "ID"), None)
    assert symbol is not None
    assert symbol.kind == "type"

    symbol = next((s for s in symbols if s.name == "UserList"), None)
    assert symbol is not None
    assert symbol.kind == "function"

GO_SOURCE = '''
package main

import "fmt"

// Person represents a person.
type Person struct {
    Name string
}

// Greet prints a greeting.
func (p *Person) Greet() {
    fmt.Println("Hello, " + p.Name)
}

// Add adds two numbers.
func Add(a, b int) int {
    return a + b
}

const MaxCount = 100
'''


def test_parse_go():
    """Test Go parsing."""
    symbols = parse_file(GO_SOURCE, "main.go", "go")
    
    # Should have type, method, function, constant
    person = next((s for s in symbols if s.name == "Person"), None)
    assert person is not None
    assert person.kind == "type"
    
    greet = next((s for s in symbols if s.name == "Greet"), None)
    assert greet is not None
    assert greet.kind == "method"


RUST_SOURCE = '''
/// A user in the system.
pub struct User {
    name: String,
}

impl User {
    /// Create a new user.
    pub fn new(name: &str) -> Self {
        Self { name: name.to_string() }
    }
    
    /// Get the user's name.
    pub fn name(&self) -> &str {
        &self.name
    }
}

pub const MAX_USERS: usize = 1000;
'''


def test_parse_rust():
    """Test Rust parsing."""
    symbols = parse_file(RUST_SOURCE, "user.rs", "rust")
    
    # Should have struct, impl, methods, constant
    user = next((s for s in symbols if s.name == "User"), None)
    assert user is not None
    assert user.kind == "type"


JAVA_SOURCE = '''
/**
 * A simple calculator.
 */
public class Calculator {
    public static final int MAX_VALUE = 100;
    
    /**
     * Add two numbers.
     */
    public int add(int a, int b) {
        return a + b;
    }
}

interface Operable {
    int operate(int a, int b);
}
'''


def test_parse_java():
    """Test Java parsing."""
    symbols = parse_file(JAVA_SOURCE, "Calculator.java", "java")

    # Should have class, method, interface
    calc = next((s for s in symbols if s.name == "Calculator"), None)
    assert calc is not None
    assert calc.kind == "class"

    add = next((s for s in symbols if s.name == "add"), None)
    assert add is not None
    assert add.kind == "method"


PHP_SOURCE = '''<?php

const MAX_RETRIES = 3;

/**
 * Authenticate a user token.
 */
function authenticate(string $token): bool
{
    return strlen($token) > 0;
}

/**
 * Manages user operations.
 */
class UserService
{
    /**
     * Get a user by ID.
     */
    public function getUser(int $userId): array
    {
        return ['id' => $userId];
    }
}

interface Authenticatable
{
    public function authenticate(string $token): bool;
}

trait Timestampable
{
    public function getCreatedAt(): string
    {
        return date(\'Y-m-d\');
    }
}

enum Status
{
    case Active;
    case Inactive;
}
'''


def test_parse_php():
    """Test PHP parsing."""
    symbols = parse_file(PHP_SOURCE, "service.php", "php")

    func = next((s for s in symbols if s.name == "authenticate"), None)
    assert func is not None
    assert func.kind == "function"
    assert "Authenticate a user token" in func.docstring

    cls = next((s for s in symbols if s.name == "UserService"), None)
    assert cls is not None
    assert cls.kind == "class"

    method = next((s for s in symbols if s.name == "getUser"), None)
    assert method is not None
    assert method.kind == "method"
    assert "Get a user by ID" in method.docstring

    interface = next((s for s in symbols if s.name == "Authenticatable"), None)
    assert interface is not None
    assert interface.kind == "type"

    trait = next((s for s in symbols if s.name == "Timestampable"), None)
    assert trait is not None
    assert trait.kind == "type"

    enum = next((s for s in symbols if s.name == "Status"), None)
    assert enum is not None
    assert enum.kind == "type"


DART_SOURCE = '''
/// Greet a user by name.
String greet(String name) {
  return 'Hello, $name!';
}

/// A simple calculator.
class Calculator {
  /// Add two numbers.
  int add(int a, int b) {
    return a + b;
  }

  /// Whether the result is positive.
  bool get isPositive => true;
}

/// Scrollable behavior for widgets.
mixin Scrollable on Calculator {
  /// Scroll to offset.
  void scrollTo(double offset) {}
}

/// Status of a task.
enum Status { pending, active, done }

/// Helpers for String manipulation.
extension StringExt on String {
  /// Whether the string is blank.
  bool get isBlank => trim().isEmpty;
}

/// A JSON map alias.
typedef JsonMap = Map<String, dynamic>;

/// An abstract repository.
abstract class Repository {
  /// Get all items.
  @override
  Future<List<String>> getAll() {
    return Future.value([]);
  }
}
'''


C_SOURCE = '''
#define MAX_USERS 100

/* Represents a user in the system. */
struct User {
    char *name;
    int age;
};

/* Status codes for operations. */
enum Status {
    STATUS_OK,
    STATUS_ERROR,
    STATUS_PENDING
};

/* Get user by ID. */
struct User *get_user(int id) {
    return NULL;
}

/* Authenticate a token string. */
int authenticate(const char *token) {
    return token != NULL;
}
'''


CPP_SOURCE = '''
#define MAX_USERS 100

namespace sample {

using UserId = int;

enum class Status {
    STATUS_ACTIVE,
    STATUS_DISABLED,
};

/* A generic value container. */
template <typename T>
class Box {
public:
    explicit Box(T value) : value_(value) {}
    ~Box() = default;

    T get() const {
        return value_;
    }

    bool operator==(const Box& other) const {
        return value_ == other.value_;
    }

private:
    T value_;
};

/* Identity for generic values. */
template <typename T>
T identity(T value) {
    return value;
}

/* Add two integers. */
int add(int a, int b);
int add(int a, int b) {
    return a + b;
}

}  // namespace sample
'''


CPP_HEADER_SOURCE = '''
namespace sample {
class Widget {
public:
    Widget();
    ~Widget();
    int Get() const;
};
}
'''


C_ONLY_HEADER_SOURCE = '''
int only_c(void) {
    int values[] = (int[]){1, 2, 3};
    return values[0];
}
'''


CPP_EDGE_SOURCE = '''
namespace outer {
namespace inner {

class Ops {
public:
    int operator[](int idx) const { return idx; }
    int operator()(int x) const { return x; }
    explicit operator bool() const { return true; }
    int value = 0;
};

int (*make_callback(int seed))(int) {
    return nullptr;
}

int consume_ref(int& v) { return v; }

}  // namespace inner
}  // namespace outer
'''


MIXED_HEADER_SOURCE = '''
class MaybeCpp {
public:
    int get() const;
};

int only_c(void) {
    int values[] = (int[]){1, 2, 3};
    return values[0];
}
'''


CXX_KEYWORDS_HEADER_SOURCE = '''
constexpr int id(int x) noexcept {
    return x;
}

[[nodiscard]] inline int succ(int x) {
    return x + 1;
}
'''


def test_parse_dart():
    """Test Dart parsing."""
    symbols = parse_file(DART_SOURCE, "app.dart", "dart")

    # Top-level function
    func = next((s for s in symbols if s.name == "greet"), None)
    assert func is not None
    assert func.kind == "function"
    assert "Greet a user by name" in func.docstring

    # Class
    cls = next((s for s in symbols if s.name == "Calculator"), None)
    assert cls is not None
    assert cls.kind == "class"
    assert "simple calculator" in cls.docstring

    # Method
    method = next((s for s in symbols if s.name == "add"), None)
    assert method is not None
    assert method.kind == "method"
    assert "Add two numbers" in method.docstring

    # Getter
    getter = next((s for s in symbols if s.name == "isPositive"), None)
    assert getter is not None
    assert getter.kind == "method"

    # Mixin
    mixin = next((s for s in symbols if s.name == "Scrollable"), None)
    assert mixin is not None
    assert mixin.kind == "class"

    # Enum
    enum = next((s for s in symbols if s.name == "Status"), None)
    assert enum is not None
    assert enum.kind == "type"

    # Extension
    ext = next((s for s in symbols if s.name == "StringExt"), None)
    assert ext is not None
    assert ext.kind == "class"

    # Typedef
    typedef = next((s for s in symbols if s.name == "JsonMap"), None)
    assert typedef is not None
    assert typedef.kind == "type"

    # Abstract class with @override decorator
    repo = next((s for s in symbols if s.name == "Repository"), None)
    assert repo is not None
    assert repo.kind == "class"
    repo_method = next((s for s in symbols if s.name == "getAll"), None)
    assert repo_method is not None
    assert repo_method.kind == "method"
    assert "@override" in repo_method.decorators

    # Qualified names
    assert method.qualified_name == "Calculator.add"
    assert getter.qualified_name == "Calculator.isPositive"


CSHARP_SOURCE = '''
using System;
using System.Collections.Generic;

namespace SampleApp
{
    /// <summary>Manages user data and operations.</summary>
    public class UserService
    {
        /// <summary>Initializes the service.</summary>
        public UserService() {}

        /// <summary>Gets a user by identifier.</summary>
        [Obsolete("Use GetUserAsync instead")]
        public string GetUser(int userId) => $"user-{userId}";

        /// <summary>Removes a user.</summary>
        public bool DeleteUser(int userId) { return true; }
    }

    /// <summary>Repository contract.</summary>
    public interface IRepository
    {
        List<string> GetAll();
    }

    /// <summary>Request status codes.</summary>
    public enum Status { Pending, Active, Done }

    /// <summary>A 2D coordinate.</summary>
    public struct Point { public int X; public int Y; }

    /// <summary>Event delegate.</summary>
    public delegate void EventCallback(object sender, EventArgs e);

    /// <summary>An immutable person record.</summary>
    public record Person(string Name, int Age);

    public class ComplexEntity
    {
        public Guid Id { get; set; }
        public readonly string Username = "admin";
        public const int MAX_AGE = 120;
        public int MultiA = 1, MultiB = 2;
        public event EventHandler OnLogin;
        ~ComplexEntity() { }
    }
}
'''


def test_parse_csharp():
    """Test C# parsing."""
    symbols = parse_file(CSHARP_SOURCE, "Sample.cs", "csharp")

    # Class
    cls = next((s for s in symbols if s.name == "UserService" and s.kind == "class"), None)
    assert cls is not None
    assert "Manages user data" in cls.docstring

    # Constructor (method inside class)
    ctor = next((s for s in symbols if s.name == "UserService" and s.kind == "method"), None)
    assert ctor is not None
    assert ctor.qualified_name == "UserService.UserService"

    # Method with attribute
    method = next((s for s in symbols if s.name == "GetUser"), None)
    assert method is not None
    assert method.kind == "method"
    assert "Gets a user" in method.docstring
    assert any("[Obsolete" in d for d in method.decorators)
    assert method.qualified_name == "UserService.GetUser"

    # Another method
    delete = next((s for s in symbols if s.name == "DeleteUser"), None)
    assert delete is not None
    assert delete.kind == "method"

    # Interface
    iface = next((s for s in symbols if s.name == "IRepository"), None)
    assert iface is not None
    assert iface.kind == "type"

    # Enum
    enum = next((s for s in symbols if s.name == "Status"), None)
    assert enum is not None
    assert enum.kind == "type"

    # Struct
    struct = next((s for s in symbols if s.name == "Point"), None)
    assert struct is not None
    assert struct.kind == "type"

    # Delegate
    delegate = next((s for s in symbols if s.name == "EventCallback"), None)
    assert delegate is not None
    assert delegate.kind == "type"

    # Record
    record = next((s for s in symbols if s.name == "Person"), None)
    assert record is not None
    assert record.kind == "class"

    # Properties, Fields, Constants, Events, Destructors
    prop = next((s for s in symbols if s.name == "Id"), None)
    assert prop is not None
    assert prop.kind == "constant"
    
    field = next((s for s in symbols if s.name == "Username"), None)
    assert field is not None
    assert field.kind == "constant"
    
    multi_field = next((s for s in symbols if s.name == "MultiA"), None)
    assert multi_field is not None
    assert multi_field.kind == "constant"
    # Note: jcodemunch implements a 1:1 mapping between AST nodes and symbols. 
    # Therefore, a single field_declaration node with multiple variable_declarators 
    # will only be indexed under the name of the first declarator (MultiA).
    assert not any(s.name == "MultiB" for s in symbols)
    
    const = next((s for s in symbols if s.name == "MAX_AGE"), None)
    assert const is not None
    assert const.kind == "constant"
    
    evt = next((s for s in symbols if s.name == "OnLogin"), None)
    assert evt is not None
    assert evt.kind == "constant"
    
    dtor = next((s for s in symbols if s.name == "ComplexEntity" and s.kind == "method"), None)
    assert dtor is not None
    assert dtor.kind == "method"


SWIFT_SOURCE = '''
/// Greet a user by name.
func greet(name: String) -> String {
    return "Hello, \\(name)!"
}

/// A simple animal.
class Animal {
    /// Initialize with a name.
    init(name: String) {}

    /// Make the animal speak.
    func speak() {}
}

/// A 2D point.
struct Point {
    var x: Double
    var y: Double
}

/// Drawable objects.
protocol Drawable {
    func draw()
}

/// Cardinal directions.
enum Direction {
    case north, south, east, west
}

let MAX_SPEED = 100
'''


def test_parse_swift():
    """Test Swift parsing."""
    symbols = parse_file(SWIFT_SOURCE, "app.swift", "swift")

    # Top-level function
    func = next((s for s in symbols if s.name == "greet"), None)
    assert func is not None
    assert func.kind == "function"
    assert "Greet a user by name" in func.docstring

    # Class
    cls = next((s for s in symbols if s.name == "Animal"), None)
    assert cls is not None
    assert cls.kind == "class"
    assert "simple animal" in cls.docstring

    # init inside class
    init = next((s for s in symbols if s.name == "init"), None)
    assert init is not None
    assert init.kind == "method"

    # Method inside class
    speak = next((s for s in symbols if s.name == "speak"), None)
    assert speak is not None
    assert speak.kind in ("function", "method")

    # Struct (maps to class)
    point = next((s for s in symbols if s.name == "Point"), None)
    assert point is not None
    assert point.kind == "class"

    # Protocol (maps to type)
    drawable = next((s for s in symbols if s.name == "Drawable"), None)
    assert drawable is not None
    assert drawable.kind == "type"

    # Enum (maps to class via class_declaration)
    direction = next((s for s in symbols if s.name == "Direction"), None)
    assert direction is not None
    assert direction.kind == "class"

    # Constant
    speed = next((s for s in symbols if s.name == "MAX_SPEED"), None)
    assert speed is not None
    assert speed.kind == "constant"


def test_parse_c():
    """Test C parsing."""
    symbols = parse_file(C_SOURCE, "sample.c", "c")

    # Should have functions
    func = next((s for s in symbols if s.name == "authenticate"), None)
    assert func is not None
    assert func.kind == "function"
    assert "Authenticate a token string" in func.docstring

    get_user = next((s for s in symbols if s.name == "get_user"), None)
    assert get_user is not None
    assert get_user.kind == "function"

    # Should have struct
    user = next((s for s in symbols if s.name == "User"), None)
    assert user is not None
    assert user.kind == "type"

    # Should have enum
    status = next((s for s in symbols if s.name == "Status"), None)
    assert status is not None
    assert status.kind == "type"

    # Should have constant
    const = next((s for s in symbols if s.name == "MAX_USERS"), None)
    assert const is not None
    assert const.kind == "constant"


def test_parse_cpp():
    """Test C++ parsing."""
    symbols = parse_file(CPP_SOURCE, "sample.cpp", "cpp")

    # Namespace-qualified class
    cls = next((s for s in symbols if s.name == "Box" and s.kind == "class"), None)
    assert cls is not None
    assert cls.qualified_name == "sample.Box"
    assert "generic value container" in cls.docstring.lower()

    # Constructor + destructor + method
    ctor = next((s for s in symbols if s.name == "Box" and s.kind == "method"), None)
    assert ctor is not None
    assert ctor.qualified_name == "sample.Box.Box"

    dtor = next((s for s in symbols if s.name == "~Box"), None)
    assert dtor is not None
    assert dtor.kind == "method"
    assert dtor.qualified_name == "sample.Box.~Box"

    get_method = next((s for s in symbols if s.name == "get"), None)
    assert get_method is not None
    assert get_method.kind == "method"
    assert get_method.qualified_name == "sample.Box.get"

    # Operator overload
    op = next((s for s in symbols if "operator" in s.name and "==" in s.name), None)
    assert op is not None
    assert op.kind == "method"

    # Type alias + enum + constant
    alias = next((s for s in symbols if s.name == "UserId"), None)
    assert alias is not None
    assert alias.kind == "type"
    assert alias.qualified_name == "sample.UserId"

    enum = next((s for s in symbols if s.name == "Status"), None)
    assert enum is not None
    assert enum.kind == "type"
    assert enum.qualified_name == "sample.Status"

    const = next((s for s in symbols if s.name == "MAX_USERS"), None)
    assert const is not None
    assert const.kind == "constant"

    # Template function signature should include template prefix.
    identity = next((s for s in symbols if s.name == "identity"), None)
    assert identity is not None
    assert identity.kind == "function"
    assert identity.qualified_name == "sample.identity"
    assert "template <typename T>" in identity.signature

    # Overload IDs should be disambiguated.
    add_symbols = [s for s in symbols if s.name == "add" and s.kind == "function"]
    assert len(add_symbols) >= 2
    add_ids = [s.id for s in add_symbols]
    assert any(i.endswith("~1") for i in add_ids)
    assert any(i.endswith("~2") for i in add_ids)


ELIXIR_SOURCE = '''
defmodule MyApp.Calculator do
  @moduledoc """
  A simple calculator module.
  """

  @type result :: {:ok, number()} | {:error, String.t()}

  @doc """
  Adds two numbers together.
  """
  def add(a, b) do
    a + b
  end

  @doc false
  defp validate(x) when is_number(x) do
    {:ok, x}
  end

  defmacro debug(expr) do
    quote do: IO.inspect(unquote(expr))
  end
end

defmodule MyApp.Types do
  @type name :: String.t()
  defguard is_positive(x) when is_number(x) and x > 0
end

defprotocol MyApp.Printable do
  @callback render(term()) :: String.t()
  def to_string(value)
end

defimpl MyApp.Printable, for: Integer do
  def to_string(value), do: Integer.to_string(value)
end
'''


def test_parse_elixir():
    """Test Elixir parsing."""
    symbols = parse_file(ELIXIR_SOURCE, "sample.ex", "elixir")

    # Module
    calc = next((s for s in symbols if s.name == "MyApp.Calculator"), None)
    assert calc is not None
    assert calc.kind == "class"
    assert "simple calculator" in calc.docstring.lower()

    # Method inside module (def)
    add = next((s for s in symbols if s.name == "add"), None)
    assert add is not None
    assert add.kind == "method"
    assert add.qualified_name == "MyApp.Calculator.add"
    assert add.parent == calc.id
    assert "Adds two numbers" in add.docstring

    # Private method (defp)
    validate = next((s for s in symbols if s.name == "validate"), None)
    assert validate is not None
    assert validate.kind == "method"
    assert validate.qualified_name == "MyApp.Calculator.validate"

    # Macro (defmacro)
    macro = next((s for s in symbols if s.name == "debug"), None)
    assert macro is not None
    assert macro.kind == "method"

    # Type alias (@type)
    result_type = next((s for s in symbols if s.name == "result"), None)
    assert result_type is not None
    assert result_type.kind == "type"
    assert result_type.qualified_name == "MyApp.Calculator.result"

    # Guard (defguard in separate module)
    guard = next((s for s in symbols if s.name == "is_positive"), None)
    assert guard is not None
    assert guard.kind == "method"
    assert guard.qualified_name == "MyApp.Types.is_positive"

    # @type in Types module
    name_type = next((s for s in symbols if s.name == "name"), None)
    assert name_type is not None
    assert name_type.kind == "type"

    # Protocol (defprotocol)
    protocol = next((s for s in symbols if s.name == "MyApp.Printable"), None)
    assert protocol is not None
    assert protocol.kind == "type"

    # @callback inside protocol
    callback = next((s for s in symbols if s.name == "render"), None)
    assert callback is not None
    assert callback.kind == "type"

    # Protocol implementation (defimpl)
    impl = next((s for s in symbols if "Printable" in s.qualified_name and s.kind == "class"), None)
    assert impl is not None

    # Function inside impl
    to_str = next((s for s in symbols if s.name == "to_string"), None)
    assert to_str is not None
    assert to_str.kind == "method"


def test_parse_cpp_header_stays_cpp():
    """C++-style headers should stay in C++ mode."""
    symbols = parse_file(CPP_HEADER_SOURCE, "sample.h", "cpp")
    assert symbols
    assert all(s.language == "cpp" for s in symbols)
    widget = next((s for s in symbols if s.name == "Widget" and s.kind == "class"), None)
    assert widget is not None
    method = next((s for s in symbols if s.name == "Get"), None)
    assert method is not None
    assert method.kind == "method"


def test_parse_cpp_header_falls_back_to_c():
    """C-only headers should fall back to C when C++ extraction fails."""
    symbols = parse_file(C_ONLY_HEADER_SOURCE, "sample.h", "cpp")
    assert symbols
    assert all(s.language == "c" for s in symbols)
    only_c = next((s for s in symbols if s.name == "only_c"), None)
    assert only_c is not None
    assert only_c.kind == "function"


def test_parse_cpp_edge_operator_and_declarator_names():
    """C++ edge declarator/operator names should be extracted and scoped."""
    symbols = parse_file(CPP_EDGE_SOURCE, "edge.cpp", "cpp")

    cls = next((s for s in symbols if s.name == "Ops" and s.kind == "class"), None)
    assert cls is not None
    assert cls.qualified_name == "outer.inner.Ops"

    op_index = next((s for s in symbols if "operator[" in s.name), None)
    assert op_index is not None
    assert op_index.kind == "method"
    assert op_index.qualified_name.startswith("outer.inner.Ops.")

    op_call = next((s for s in symbols if "operator(" in s.name), None)
    assert op_call is not None
    assert op_call.kind == "method"

    op_conv = next((s for s in symbols if "operator bool" in s.name), None)
    assert op_conv is not None
    assert op_conv.kind == "method"

    callback = next((s for s in symbols if s.name == "make_callback"), None)
    assert callback is not None
    assert callback.kind == "function"
    assert callback.qualified_name == "outer.inner.make_callback"

    consume_ref = next((s for s in symbols if s.name == "consume_ref"), None)
    assert consume_ref is not None
    assert consume_ref.kind == "function"
    assert consume_ref.qualified_name == "outer.inner.consume_ref"


def test_parse_cpp_declaration_filter_ignores_variables():
    """Variable declarations should not be indexed as functions in C++."""
    symbols = parse_file(CPP_EDGE_SOURCE, "edge.cpp", "cpp")
    variable_names = {"value"}
    assert all(s.name not in variable_names for s in symbols)


def test_parse_cpp_mixed_header_deterministic_selection():
    """Mixed C/C++ headers should produce deterministic language selection."""
    run1 = parse_file(MIXED_HEADER_SOURCE, "mixed.h", "cpp")
    run2 = parse_file(MIXED_HEADER_SOURCE, "mixed.h", "cpp")

    assert run1 and run2
    langs1 = {s.language for s in run1}
    langs2 = {s.language for s in run2}
    assert langs1 == langs2
    assert len(langs1) == 1


def test_parse_cpp_header_with_cpp_keywords_stays_cpp():
    """C++ keywords in headers should strongly select C++ parsing."""
    symbols = parse_file(CXX_KEYWORDS_HEADER_SOURCE, "keywords.h", "cpp")
    assert symbols
    assert all(s.language == "cpp" for s in symbols)
    names = {s.name for s in symbols if s.kind in {"function", "method"}}
    assert "id" in names
    assert "succ" in names


RUBY_SOURCE = '''\
# Serialization helpers.
module Serializable
  def serialize
    {}
  end
end

# Represents a user.
class User
  include Serializable

  def initialize(name, email)
    @name = name
    @email = email
  end

  # Finds a user by ID.
  def self.find(id)
    nil
  end

  def greet
    "Hello, #{@name}!"
  end

  private

  def valid_email?
    @email.include?('@')
  end
end

# Top-level helper.
def format_name(first, last)
  "#{first} #{last}"
end
'''


def test_parse_ruby():
    """Test Ruby parsing."""
    symbols = parse_file(RUBY_SOURCE, "sample.rb", "ruby")

    # Module → type
    mod = next((s for s in symbols if s.name == "Serializable"), None)
    assert mod is not None
    assert mod.kind == "type"
    assert "Serialization" in mod.docstring

    # Method inside module
    serialize = next((s for s in symbols if s.name == "serialize"), None)
    assert serialize is not None
    assert serialize.kind == "method"
    assert serialize.qualified_name == "Serializable.serialize"

    # Class
    cls = next((s for s in symbols if s.name == "User"), None)
    assert cls is not None
    assert cls.kind == "class"
    assert "Represents" in cls.docstring

    # Instance method
    init = next((s for s in symbols if s.name == "initialize"), None)
    assert init is not None
    assert init.kind == "method"
    assert init.qualified_name == "User.initialize"
    assert init.parent == cls.id

    # Singleton method (def self.find)
    find = next((s for s in symbols if s.name == "find"), None)
    assert find is not None
    assert find.kind == "method"
    assert find.qualified_name == "User.find"
    assert "Finds a user" in find.docstring

    # Private method
    valid = next((s for s in symbols if s.name == "valid_email?"), None)
    assert valid is not None
    assert valid.kind == "method"

    # Top-level function
    fmt = next((s for s in symbols if s.name == "format_name"), None)
    assert fmt is not None
    assert fmt.kind == "function"
    assert fmt.qualified_name == "format_name"
    assert "Top-level" in fmt.docstring


PERL_SOURCE = '''
package Animal;

# Create a new Animal
sub new {
    my ($class, %args) = @_;
    return bless \\%args, $class;
}

=pod

=head1 describe

Returns a description of the animal.

=cut

sub describe {
    my $self = shift;
    return "$self->{name} is a $self->{species}";
}

use constant MAX_LEGS => 4;
use constant KINGDOM => "Animalia";

package main;

sub run {
    my $animal = Animal->new(name => "Dog", species => "Canis");
    print $animal->describe();
}
'''


def test_parse_perl():
    """Test Perl parsing."""
    symbols = parse_file(PERL_SOURCE, "sample.pl", "perl")
    assert len(symbols) > 0

    # Packages
    animal_pkg = next((s for s in symbols if s.name == "Animal"), None)
    assert animal_pkg is not None
    assert animal_pkg.kind == "class"

    # Subroutines
    new_sub = next((s for s in symbols if s.name == "new"), None)
    assert new_sub is not None
    assert new_sub.kind == "function"
    assert "Create a new Animal" in new_sub.docstring

    describe_sub = next((s for s in symbols if s.name == "describe"), None)
    assert describe_sub is not None
    assert "Returns a description" in describe_sub.docstring

    # Constants
    max_legs = next((s for s in symbols if s.name == "MAX_LEGS"), None)
    assert max_legs is not None
    assert max_legs.kind == "constant"

    kingdom = next((s for s in symbols if s.name == "KINGDOM"), None)
    assert kingdom is not None
    assert kingdom.kind == "constant"


KOTLIN_SOURCE = '''
package com.example

// A simple greeter
fun greet(name: String): String = "Hello, $name"

/**
 * A minimal calculator.
 */
class Calculator {
    // Add two numbers
    fun add(a: Int, b: Int): Int = a + b

    private fun reset(): Unit {}
}

interface Clickable {
    fun onClick()
}

object AppConfig {
    fun getInstance(): AppConfig = this
}

typealias StringList = List<String>

enum class Direction { NORTH, SOUTH, EAST, WEST }

data class Point(val x: Int, val y: Int)
'''


def test_parse_kotlin():
    """Test Kotlin parsing."""
    symbols = parse_file(KOTLIN_SOURCE, "Main.kt", "kotlin")

    # Top-level function
    func = next((s for s in symbols if s.name == "greet"), None)
    assert func is not None
    assert func.kind == "function"
    assert "greet" in func.signature
    # Note: comment before fun after package decl is absorbed into package_header by the Kotlin grammar

    # Class
    cls = next((s for s in symbols if s.name == "Calculator"), None)
    assert cls is not None
    assert cls.kind == "class"
    assert "Calculator" in cls.signature

    # Method inside class
    add = next((s for s in symbols if s.name == "add"), None)
    assert add is not None
    assert add.kind == "method"
    assert add.qualified_name == "Calculator.add"
    assert "Add two numbers" in add.docstring

    # Interface
    iface = next((s for s in symbols if s.name == "Clickable"), None)
    assert iface is not None
    assert iface.kind == "class"
    assert "interface" in iface.signature

    # Object declaration
    obj = next((s for s in symbols if s.name == "AppConfig"), None)
    assert obj is not None
    assert obj.kind == "class"
    assert "object" in obj.signature

    # Type alias
    alias = next((s for s in symbols if s.name == "StringList"), None)
    assert alias is not None
    assert alias.kind == "type"
    assert "typealias" in alias.signature

    # Enum class
    enum = next((s for s in symbols if s.name == "Direction"), None)
    assert enum is not None
    assert enum.kind == "class"
    assert "enum" in enum.signature

    # Data class
    point = next((s for s in symbols if s.name == "Point"), None)
    assert point is not None
    assert point.kind == "class"
    assert "data" in point.signature

    # .kts extension also maps to kotlin
    from jcodemunch_mcp.parser.languages import get_language_for_path
    assert get_language_for_path("build.gradle.kts") == "kotlin"
    assert get_language_for_path("Main.kt") == "kotlin"


GLEAM_SOURCE = '''
pub type Color {
  Red
  Green
  Blue
}

pub type Alias = String

pub const max_size: Int = 100

// Greet a user
pub fn greet(name: String) -> String {
  "Hello, " <> name
}

fn helper(x: Int) -> Int {
  x + 1
}
'''

BASH_SOURCE = '''#!/bin/bash
# Deploy the app
function deploy() {
  echo "Deploying $1"
}

# Build everything
build() {
  make all
}

readonly VERSION="1.0"
'''

NIX_SOURCE = '''
let
  # Greet someone
  greet = name: "Hello, ${name}";
  # Add two numbers
  add = x: y: x + y;
  version = "1.0";
in { inherit greet; }
'''


def test_parse_gleam():
    """Test Gleam parsing."""
    symbols = parse_file(GLEAM_SOURCE, "app.gleam", "gleam")

    typ = next((s for s in symbols if s.name == "Color"), None)
    assert typ is not None
    assert typ.kind == "type"
    assert "Color" in typ.signature

    alias = next((s for s in symbols if s.name == "Alias"), None)
    assert alias is not None
    assert alias.kind == "type"
    assert "typealias" in alias.signature.lower() or "Alias" in alias.signature

    const = next((s for s in symbols if s.name == "max_size"), None)
    assert const is not None
    assert const.kind == "constant"

    fn = next((s for s in symbols if s.name == "greet"), None)
    assert fn is not None
    assert fn.kind == "function"
    assert "String" in fn.signature
    assert "Greet a user" in fn.docstring

    priv = next((s for s in symbols if s.name == "helper"), None)
    assert priv is not None
    assert priv.kind == "function"

    from jcodemunch_mcp.parser.languages import get_language_for_path
    assert get_language_for_path("app.gleam") == "gleam"


def test_parse_bash():
    """Test Bash parsing."""
    symbols = parse_file(BASH_SOURCE, "script.sh", "bash")

    deploy = next((s for s in symbols if s.name == "deploy"), None)
    assert deploy is not None
    assert deploy.kind == "function"
    assert "Deploy the app" in deploy.docstring

    build = next((s for s in symbols if s.name == "build"), None)
    assert build is not None
    assert build.kind == "function"
    assert "Build everything" in build.docstring

    from jcodemunch_mcp.parser.languages import get_language_for_path
    assert get_language_for_path("script.sh") == "bash"
    assert get_language_for_path("script.bash") == "bash"


def test_parse_nix():
    """Test Nix parsing."""
    symbols = parse_file(NIX_SOURCE, "default.nix", "nix")

    greet = next((s for s in symbols if s.name == "greet"), None)
    assert greet is not None
    assert greet.kind == "function"
    assert "Greet someone" in greet.docstring

    add = next((s for s in symbols if s.name == "add"), None)
    assert add is not None
    assert add.kind == "function"
    assert "Add two numbers" in add.docstring

    version = next((s for s in symbols if s.name == "version"), None)
    assert version is not None
    assert version.kind == "constant"

    from jcodemunch_mcp.parser.languages import get_language_for_path
    assert get_language_for_path("default.nix") == "nix"


VUE_JS_SOURCE = '''<template>
  <div>{{ message }}</div>
</template>

<script setup>
import { ref } from 'vue'

// Greet a user
function greet(name) {
  return `Hello, ${name}`
}

class MyComponent {
  mounted() {}
}
</script>
'''

VUE_TS_SOURCE = '''<template><div /></template>
<script setup lang="ts">
import { ref } from 'vue'

interface User { name: string }

// Get the user name
function getName(user: User): string {
  return user.name
}
</script>
'''


def test_parse_vue_js():
    """Test Vue SFC parsing with JavaScript script block."""
    symbols = parse_file(VUE_JS_SOURCE, "App.vue", "vue")

    fn = next((s for s in symbols if s.name == "greet"), None)
    assert fn is not None
    assert fn.kind == "function"
    assert fn.language == "vue"
    assert "Greet a user" in fn.docstring
    assert fn.line == 9  # correct line in .vue file

    cls = next((s for s in symbols if s.name == "MyComponent"), None)
    assert cls is not None
    assert cls.kind == "class"

    from jcodemunch_mcp.parser.languages import get_language_for_path
    assert get_language_for_path("App.vue") == "vue"


def test_parse_vue_ts():
    """Test Vue SFC parsing with TypeScript script block (lang="ts")."""
    symbols = parse_file(VUE_TS_SOURCE, "User.vue", "vue")

    iface = next((s for s in symbols if s.name == "User" and s.kind == "type"), None)
    assert iface is not None
    assert iface.language == "vue"

    fn = next((s for s in symbols if s.name == "getName"), None)
    assert fn is not None
    assert fn.kind == "function"
    assert "string" in fn.signature
    assert "Get the user name" in fn.docstring


def test_parse_vue_no_script():
    """Test Vue SFC with no script block returns empty symbol list."""
    source = "<template><div>hello</div></template>"
    symbols = parse_file(source, "Static.vue", "vue")
    assert symbols == []


EJS_SOURCE = '''<!DOCTYPE html>
<html>
<head><title><%= title %></title></head>
<body>
  <%- include('partials/header', { user: user }) %>

  <% function formatDate(date) { %>
    <span><%= date.toLocaleDateString() %></span>
  <% } %>

  <%- include('partials/footer') %>
</body>
</html>
'''


def test_parse_ejs():
    """Test EJS template parsing."""
    symbols = parse_file(EJS_SOURCE, "views/index.ejs", "ejs")

    # Synthetic template symbol always present
    tmpl = next((s for s in symbols if s.kind == "template"), None)
    assert tmpl is not None
    assert tmpl.name == "index"
    assert tmpl.language == "ejs"

    # JS function inside scriptlet block
    fn = next((s for s in symbols if s.name == "formatDate"), None)
    assert fn is not None
    assert fn.kind == "function"
    assert "formatDate(date)" in fn.signature

    # Include references
    header = next((s for s in symbols if s.name == "partials/header"), None)
    assert header is not None
    assert header.kind == "import"

    footer = next((s for s in symbols if s.name == "partials/footer"), None)
    assert footer is not None

    from jcodemunch_mcp.parser.languages import get_language_for_path
    assert get_language_for_path("views/index.ejs") == "ejs"


def test_parse_ejs_no_scriptlets():
    """EJS file with no scriptlets still produces the template symbol."""
    source = "<html><body><h1>Hello</h1></body></html>"
    symbols = parse_file(source, "static.ejs", "ejs")
    assert len(symbols) == 1
    assert symbols[0].kind == "template"
    assert symbols[0].name == "static"


# ---------------------------------------------------------------------------
# Verse (UEFN)
# ---------------------------------------------------------------------------

_VERSE_SOURCE = """\
# Module import path: /Fortnite.com/UI
UI<public> := module:

    # Base UI element
    widget<public><abstract> := class<concrete>():
        # Get the widget's visibility
        GetVisibility<public>()<transacts>:widget_visibility = external {}

        var Opacity<public>:float = external {}

    # Extension method
    (W:widget).SetVisible<public>(Visible:logic)<transacts>:void = external {}

    EWidgetColor<public> := enum:
        Red
        Green
        Blue
"""


def test_parse_verse():
    """Test Verse (UEFN) language parsing."""
    symbols = parse_file(_VERSE_SOURCE, "Fortnite.digest.verse", "verse")

    # Module container
    ui = next((s for s in symbols if s.name == "UI"), None)
    assert ui is not None
    assert ui.kind == "class"
    assert "UI" in ui.signature
    assert "module" in ui.signature
    assert ui.language == "verse"

    # Nested class
    widget = next((s for s in symbols if s.name == "widget"), None)
    assert widget is not None
    assert widget.kind == "class"
    assert widget.parent is not None  # parented to UI module

    # Method inside class
    get_vis = next((s for s in symbols if s.name == "GetVisibility"), None)
    assert get_vis is not None
    assert get_vis.kind == "method"
    assert "GetVisibility" in get_vis.signature
    assert "<transacts>" in get_vis.signature
    assert "widget_visibility" in get_vis.signature
    assert get_vis.docstring == "Get the widget's visibility"

    # Variable declaration
    opacity = next((s for s in symbols if s.name == "Opacity"), None)
    assert opacity is not None
    assert opacity.kind == "constant"
    assert "float" in opacity.signature

    # Extension method
    set_visible = next((s for s in symbols if s.name == "SetVisible"), None)
    assert set_visible is not None
    assert set_visible.kind == "method"
    assert "SetVisible" in set_visible.qualified_name

    # Enum type
    color = next((s for s in symbols if s.name == "EWidgetColor"), None)
    assert color is not None
    assert color.kind == "type"
    assert "enum" in color.signature

    from jcodemunch_mcp.parser.languages import get_language_for_path
    assert get_language_for_path("Fortnite.digest.verse") == "verse"


def test_parse_verse_utf8_byte_offsets():
    """Byte offsets must be byte positions, not char positions (smart quotes are 3 bytes each)."""
    # U+2019 RIGHT SINGLE QUOTATION MARK = 3 bytes in UTF-8
    source = "# Widget\u2019s tooltip\nMyClass<public> := class<concrete>():\n    Init<public>():void = external {}\n"
    source_bytes = source.encode("utf-8")
    symbols = parse_file(source, "test.verse", "verse")

    cls = next((s for s in symbols if s.name == "MyClass"), None)
    assert cls is not None
    # byte_offset must be a valid byte position in the encoded source
    assert cls.byte_offset >= 0
    assert cls.byte_offset + cls.byte_length <= len(source_bytes)
    # Content at the byte range should start with the declaration
    chunk = source_bytes[cls.byte_offset:cls.byte_offset + 7]
    assert chunk == b"MyClass"


ERLANG_SOURCE = '''
-module(math_utils).
-export([add/2, factorial/1]).

-type number_pair() :: {integer(), integer()}.
-record(point, {x = 0 :: integer(), y = 0 :: integer()}).
-define(MAX_ITER, 1000).

%% Adds two integers together.
add(A, B) ->
    A + B.

%% Computes the factorial of N.
factorial(0) -> 1;
factorial(N) when N > 0 -> N * factorial(N - 1).

-spec add(integer(), integer()) -> integer().
'''


def test_parse_erlang_functions():
    """Functions are extracted with correct name, arity-qualified_name, and signature."""
    symbols = parse_file(ERLANG_SOURCE, "math_utils.erl", "erlang")

    add = next((s for s in symbols if s.name == "add"), None)
    assert add is not None
    assert add.kind == "function"
    assert add.qualified_name == "add/2"
    assert "add" in add.signature
    assert add.language == "erlang"
    assert "Adds two integers" in add.docstring


def test_parse_erlang_multiclauses_merged():
    """Multi-clause functions produce exactly one symbol spanning all clauses."""
    symbols = parse_file(ERLANG_SOURCE, "math_utils.erl", "erlang")

    fac_syms = [s for s in symbols if s.name == "factorial"]
    assert len(fac_syms) == 1, "multi-clause function should produce exactly one symbol"
    fac = fac_syms[0]
    assert fac.kind == "function"
    assert fac.qualified_name == "factorial/1"
    # end_line must span past the first clause
    assert fac.end_line > fac.line
    assert "Computes the factorial" in fac.docstring


def test_parse_erlang_type():
    """type_alias declarations are extracted as 'type' symbols."""
    symbols = parse_file(ERLANG_SOURCE, "math_utils.erl", "erlang")

    typ = next((s for s in symbols if s.name == "number_pair"), None)
    assert typ is not None
    assert typ.kind == "type"
    assert "number_pair" in typ.signature


def test_parse_erlang_record():
    """-record declarations are extracted as 'type' symbols."""
    symbols = parse_file(ERLANG_SOURCE, "math_utils.erl", "erlang")

    rec = next((s for s in symbols if s.name == "point"), None)
    assert rec is not None
    assert rec.kind == "type"
    assert "record" in rec.signature.lower()


def test_parse_erlang_define():
    """-define macros are extracted as 'constant' symbols."""
    symbols = parse_file(ERLANG_SOURCE, "math_utils.erl", "erlang")

    macro = next((s for s in symbols if s.name == "MAX_ITER"), None)
    assert macro is not None
    assert macro.kind == "constant"


def test_parse_erlang_byte_offsets():
    """Byte offsets must be valid positions within the encoded source."""
    source_bytes = ERLANG_SOURCE.encode("utf-8")
    symbols = parse_file(ERLANG_SOURCE, "math_utils.erl", "erlang")

    for sym in symbols:
        assert sym.byte_offset >= 0
        assert sym.byte_offset + sym.byte_length <= len(source_bytes)


def test_erlang_extension_mapping():
    """Both .erl and .hrl map to the 'erlang' language."""
    from jcodemunch_mcp.parser.languages import get_language_for_path
    assert get_language_for_path("math_utils.erl") == "erlang"
    assert get_language_for_path("include/defs.hrl") == "erlang"


FORTRAN_SOURCE = '''
! Computes the sum of two integers.
function add(a, b) result(res)
  integer, intent(in) :: a, b
  integer :: res
  res = a + b
end function add

! Greets the user.
subroutine greet(name)
  character(len=*), intent(in) :: name
  print *, "Hello, " // name
end subroutine greet

module math_utils
  implicit none
  integer, parameter :: MAX_SIZE = 100
  type :: Point
    real :: x
    real :: y
  end type Point
contains
  ! Multiplies two reals.
  function multiply(a, b) result(res)
    real, intent(in) :: a, b
    real :: res
    res = a * b
  end function multiply
end module math_utils
'''


def test_parse_fortran_function():
    """Top-level functions are extracted with docstrings."""
    symbols = parse_file(FORTRAN_SOURCE, "math.f90", "fortran")

    add = next((s for s in symbols if s.name == "add"), None)
    assert add is not None
    assert add.kind == "function"
    assert "add" in add.signature
    assert add.parent is None
    assert "Computes the sum" in add.docstring
    assert add.language == "fortran"


def test_parse_fortran_subroutine():
    """Top-level subroutines are extracted as function-kind symbols."""
    symbols = parse_file(FORTRAN_SOURCE, "math.f90", "fortran")

    greet = next((s for s in symbols if s.name == "greet"), None)
    assert greet is not None
    assert greet.kind == "function"
    assert "subroutine" in greet.signature
    assert "Greets the user" in greet.docstring


def test_parse_fortran_module():
    """Modules are extracted as class-kind symbols."""
    symbols = parse_file(FORTRAN_SOURCE, "math.f90", "fortran")

    mod = next((s for s in symbols if s.name == "math_utils"), None)
    assert mod is not None
    assert mod.kind == "class"
    assert "module" in mod.signature
    assert mod.parent is None


def test_parse_fortran_module_method():
    """Procedures inside a module are extracted as methods with the module as parent."""
    symbols = parse_file(FORTRAN_SOURCE, "math.f90", "fortran")

    mul = next((s for s in symbols if s.name == "multiply"), None)
    assert mul is not None
    assert mul.kind == "method"
    assert mul.parent == "math_utils"
    assert mul.qualified_name == "math_utils::multiply"
    assert "Multiplies two reals" in mul.docstring


def test_parse_fortran_derived_type():
    """Derived type definitions inside modules are extracted as type symbols."""
    symbols = parse_file(FORTRAN_SOURCE, "math.f90", "fortran")

    pt = next((s for s in symbols if s.name == "Point"), None)
    assert pt is not None
    assert pt.kind == "type"
    assert pt.parent == "math_utils"
    assert "Point" in pt.signature


def test_parse_fortran_parameter_constant():
    """Parameter constants inside modules are extracted as constant symbols."""
    symbols = parse_file(FORTRAN_SOURCE, "math.f90", "fortran")

    const = next((s for s in symbols if s.name == "MAX_SIZE"), None)
    assert const is not None
    assert const.kind == "constant"
    assert const.parent == "math_utils"
    assert const.qualified_name == "math_utils::MAX_SIZE"


def test_parse_fortran_byte_offsets():
    """Byte offsets must be valid positions within the encoded source."""
    source_bytes = FORTRAN_SOURCE.encode("utf-8")
    symbols = parse_file(FORTRAN_SOURCE, "math.f90", "fortran")

    for sym in symbols:
        assert sym.byte_offset >= 0
        assert sym.byte_offset + sym.byte_length <= len(source_bytes)


def test_fortran_extension_mapping():
    """Common Fortran file extensions map to the 'fortran' language."""
    from jcodemunch_mcp.parser.languages import get_language_for_path
    for ext in (".f90", ".f95", ".f03", ".f08", ".f", ".for", ".fpp"):
        assert get_language_for_path(f"code{ext}") == "fortran", ext


# ---------------------------------------------------------------------------
# Vue SFC
# ---------------------------------------------------------------------------

VUE_COMPOSITION_SOURCE = """\
<script setup lang="ts">
import { ref, computed } from 'vue'

// Current count
const count = ref(0)
const doubled = computed(() => count.value * 2)
const props = defineProps<{ title: string; max?: number }>()
const emit = defineEmits(['update'])

function increment() {
  count.value++
  emit('update', count.value)
}

function reset() {
  count.value = 0
}
</script>
<template><div>{{ count }}</div></template>
"""

VUE_OPTIONS_SOURCE = """\
<script>
export default {
  props: { items: Array, loading: Boolean },
  data() { return { selected: null } },
  computed: {
    count() { return this.items.length },
    hasItems() { return this.count > 0 }
  },
  methods: {
    select(item) { this.selected = item },
    clear() { this.selected = null }
  }
}
</script>
<template><div/></template>
"""


def test_vue_composition_component_symbol():
    """Component name extracted from filename as class symbol."""
    syms = parse_file(VUE_COMPOSITION_SOURCE, "Counter.vue", "vue")
    comp = [s for s in syms if s.kind == "class" and s.name == "Counter"]
    assert len(comp) == 1
    assert comp[0].line == 1


def test_vue_composition_ref_captured():
    """ref() declarations captured as constants."""
    syms = parse_file(VUE_COMPOSITION_SOURCE, "Counter.vue", "vue")
    names = [s.name for s in syms if s.kind == "constant"]
    assert "count" in names
    assert "doubled" in names


def test_vue_composition_define_macros():
    """defineProps and defineEmits captured as constants."""
    syms = parse_file(VUE_COMPOSITION_SOURCE, "Counter.vue", "vue")
    names = [s.name for s in syms if s.kind == "constant"]
    assert "props" in names
    assert "emit" in names


def test_vue_composition_functions():
    """Function declarations captured with correct line numbers."""
    syms = parse_file(VUE_COMPOSITION_SOURCE, "Counter.vue", "vue")
    funcs = {s.name: s for s in syms if s.kind == "function"}
    assert "increment" in funcs
    assert "reset" in funcs
    assert funcs["increment"].line > 1


def test_vue_composition_parent_relationship():
    """All symbols have the component as parent."""
    syms = parse_file(VUE_COMPOSITION_SOURCE, "Counter.vue", "vue")
    comp = next(s for s in syms if s.kind == "class")
    children = [s for s in syms if s.parent == comp.id]
    assert len(children) >= 4


def test_vue_options_methods():
    """Options API methods extracted as method symbols."""
    syms = parse_file(VUE_OPTIONS_SOURCE, "MyList.vue", "vue")
    methods = {s.name for s in syms if s.kind == "method"}
    assert "select" in methods
    assert "clear" in methods


def test_vue_options_computed():
    """Options API computed properties extracted as method symbols."""
    syms = parse_file(VUE_OPTIONS_SOURCE, "MyList.vue", "vue")
    computed = {s.name for s in syms if s.kind == "method"}
    assert "count" in computed
    assert "hasItems" in computed


def test_vue_options_props():
    """Options API props captured as constant."""
    syms = parse_file(VUE_OPTIONS_SOURCE, "MyList.vue", "vue")
    props = [s for s in syms if s.name == "props" and s.kind == "constant"]
    assert len(props) == 1


def test_vue_extension_mapping():
    """.vue extension maps to vue language."""
    from jcodemunch_mcp.parser.languages import get_language_for_path
    assert get_language_for_path("src/components/Counter.vue") == "vue"


XML_SOURCE = '''\
<?xml version="1.0" encoding="UTF-8"?>
<!-- Configuration for the sample application -->
<config id="app-config" version="2.0">
  <!-- Database connection settings -->
  <database id="db-primary" type="postgresql">
    <host>localhost</host>
    <port>5432</port>
  </database>
  <!-- Cache layer -->
  <cache id="redis-cache" driver="redis">
    <ttl>3600</ttl>
  </cache>
  <script type="text/javascript" src="config/validator.js"/>
  <script type="text/javascript" src="lib/helpers.js"/>
</config>
'''

XUL_SOURCE = '''\
<?xml version="1.0"?>
<!-- Main application window -->
<window id="main-window" title="My Application"
        xmlns="http://www.mozilla.org/keymaster/gatekeeper/there.is.only.xul">
  <!-- Search controls -->
  <textbox id="search-input" placeholder="Search..."/>
  <button id="search-button" label="Search"/>
  <!-- Options menu -->
  <menulist id="options-menu">
    <menupopup>
      <menuitem label="Option 1"/>
      <menuitem label="Option 2"/>
    </menupopup>
  </menulist>
  <script type="application/javascript" src="chrome://app/content/main.js"/>
  <script type="application/javascript" src="utils.js"/>
</window>
'''


def test_parse_xml():
    """Test XML parsing."""
    symbols = parse_file(XML_SOURCE, "sample.xml", "xml")

    # Root element → type
    root = next((s for s in symbols if s.name == "config" and s.kind == "type"), None)
    assert root is not None
    assert "config" in root.signature

    # Root also has id attribute → constant
    root_id = next((s for s in symbols if s.name == "app-config" and s.kind == "constant"), None)
    assert root_id is not None

    # Elements with id attributes → constants
    db = next((s for s in symbols if s.name == "db-primary"), None)
    assert db is not None
    assert db.kind == "constant"
    assert "Database connection" in db.docstring

    cache = next((s for s in symbols if s.name == "redis-cache"), None)
    assert cache is not None
    assert cache.kind == "constant"
    assert "Cache layer" in cache.docstring

    # Script references → functions
    validator = next((s for s in symbols if s.name == "validator.js"), None)
    assert validator is not None
    assert validator.kind == "function"
    assert validator.qualified_name == "config/validator.js"

    helpers = next((s for s in symbols if s.name == "helpers.js"), None)
    assert helpers is not None
    assert helpers.kind == "function"

    # All symbols should be xml language
    assert all(s.language == "xml" for s in symbols)


def test_parse_xul():
    """Test XUL parsing (parsed as XML)."""
    symbols = parse_file(XUL_SOURCE, "app.xul", "xml")

    # Root window → type
    window = next((s for s in symbols if s.name == "window" and s.kind == "type"), None)
    assert window is not None
    assert "window" in window.signature
    assert "Main application window" in window.docstring

    # UI elements with ids → constants
    search_input = next((s for s in symbols if s.name == "search-input"), None)
    assert search_input is not None
    assert search_input.kind == "constant"
    assert "Search controls" in search_input.docstring

    search_btn = next((s for s in symbols if s.name == "search-button"), None)
    assert search_btn is not None
    assert search_btn.kind == "constant"

    options_menu = next((s for s in symbols if s.name == "options-menu"), None)
    assert options_menu is not None
    assert options_menu.kind == "constant"

    # Script references → functions
    main_js = next((s for s in symbols if s.name == "main.js"), None)
    assert main_js is not None
    assert main_js.kind == "function"
    assert main_js.qualified_name == "chrome://app/content/main.js"

    utils_js = next((s for s in symbols if s.name == "utils.js"), None)
    assert utils_js is not None
    assert utils_js.kind == "function"

    # Window root has id → also produces a constant
    main_window_id = next((s for s in symbols if s.name == "main-window" and s.kind == "constant"), None)
    assert main_window_id is not None

    # All xml language
    assert all(s.language == "xml" for s in symbols)


def test_xml_extension_mapping():
    """.xml and .xul extensions map to xml language."""
    from jcodemunch_mcp.parser.languages import get_language_for_path
    assert get_language_for_path("config/settings.xml") == "xml"
    assert get_language_for_path("ui/main.xul") == "xml"
    assert get_language_for_path("data/UPPER.XML") == "xml"
