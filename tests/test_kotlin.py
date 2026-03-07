"""Kotlin language parser tests — covers the full Kotlin 2.x language surface."""

import pytest
from src.jcodemunch_mcp.parser.extractor import parse_file
from src.jcodemunch_mcp.parser.languages import LANGUAGE_EXTENSIONS, LANGUAGE_REGISTRY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def symbols_by_kind(source: str, kind: str) -> list[str]:
    syms = parse_file(source, "Test.kt", "kotlin")
    return [s.name for s in syms if s.kind == kind]


def all_names(source: str) -> list[str]:
    return [s.name for s in parse_file(source, "Test.kt", "kotlin")]


def sym_map(source: str) -> dict[str, object]:
    """Return {name: Symbol} dict for easy assertions."""
    return {s.name: s for s in parse_file(source, "Test.kt", "kotlin")}


# ---------------------------------------------------------------------------
# 1. Extension registration
# ---------------------------------------------------------------------------

def test_kotlin_extensions_registered():
    assert LANGUAGE_EXTENSIONS[".kt"] == "kotlin"
    assert LANGUAGE_EXTENSIONS[".kts"] == "kotlin"


def test_kotlin_in_registry():
    assert "kotlin" in LANGUAGE_REGISTRY


# ---------------------------------------------------------------------------
# 2. Classes — all modifiers
# ---------------------------------------------------------------------------

CLASS_VARIANTS = """\
class Plain
abstract class AbstractBase
open class OpenClass
data class UserDto(val id: Long, val name: String)
sealed class SealedResult
sealed interface SealedIface
annotation class Inject(val optional: Boolean = false)
enum class Status { ACTIVE, INACTIVE }
@JvmInline value class Password(val raw: String)
"""

# NOTE: `fun interface` produces has_error=True in the current tree-sitter-kotlin grammar
# and cannot be indexed until the grammar is updated.
FUN_INTERFACE_GRAMMAR_LIMITATION = True


def test_class_variants_indexed():
    names = all_names(CLASS_VARIANTS)
    for expected in [
        "Plain", "AbstractBase", "OpenClass", "UserDto",
        "SealedResult", "SealedIface", "Inject", "Status",
        "Password",
    ]:
        assert expected in names, f"Missing class: {expected}"


def test_class_kinds():
    syms = sym_map(CLASS_VARIANTS)
    for name in ["Plain", "AbstractBase", "UserDto", "SealedResult", "Password"]:
        assert syms[name].kind == "class", f"{name} should be kind=class"


# ---------------------------------------------------------------------------
# 3. Object declarations (singletons + data objects)
# ---------------------------------------------------------------------------

OBJECTS = """\
object AppConfig {
    const val BASE_URL = "https://api.example.com"
    val TIMEOUT_MS = 5000L
    fun isDebug(): Boolean = true
}

data object Singleton {
    fun create(): Singleton = Singleton
}
"""


def test_objects_indexed():
    names = all_names(OBJECTS)
    assert "AppConfig" in names
    assert "Singleton" in names


def test_object_kind_is_class():
    syms = sym_map(OBJECTS)
    assert syms["AppConfig"].kind == "class"
    assert syms["Singleton"].kind == "class"


def test_methods_inside_object():
    names = all_names(OBJECTS)
    assert "isDebug" in names
    assert "create" in names


def test_constants_inside_object():
    names = all_names(OBJECTS)
    assert "BASE_URL" in names
    assert "TIMEOUT_MS" in names


# ---------------------------------------------------------------------------
# 4. Companion objects
# ---------------------------------------------------------------------------

COMPANION = """\
class MyClass(val value: Int) {
    companion object {
        const val DEFAULT = 0
        val MAX_VALUE = 100
        val ignored = "not-upper"
        fun create(v: Int): MyClass = MyClass(v)
    }
}

class MyNamed {
    companion object Factory {
        fun make(): MyNamed = MyNamed()
    }
}
"""


def test_anonymous_companion_indexed():
    names = all_names(COMPANION)
    assert "Companion" in names


def test_named_companion_indexed():
    names = all_names(COMPANION)
    assert "Factory" in names


def test_companion_methods_scoped_to_companion():
    syms = {s.name: s for s in parse_file(COMPANION, "Test.kt", "kotlin")}
    create = syms.get("create")
    assert create is not None, "create() not indexed"
    assert create.kind == "method"
    assert "Companion" in (create.qualified_name or "")


def test_named_companion_methods_scoped():
    syms = {s.name: s for s in parse_file(COMPANION, "Test.kt", "kotlin")}
    make = syms.get("make")
    assert make is not None
    assert "Factory" in (make.qualified_name or "")


def test_companion_constants_extracted():
    names = all_names(COMPANION)
    assert "DEFAULT" in names
    assert "MAX_VALUE" in names


def test_companion_lowercase_val_not_extracted():
    names = all_names(COMPANION)
    assert "ignored" not in names


# ---------------------------------------------------------------------------
# 5. Sealed classes / interfaces and subclasses
# ---------------------------------------------------------------------------

SEALED = """\
sealed class Result<out T> {
    data class Success<T>(val data: T) : Result<T>()
    data class Failure(val error: String) : Result<Nothing>()
    object Loading : Result<Nothing>()
}

sealed interface Shape {
    data class Circle(val radius: Double) : Shape
    data object Square : Shape
}
"""


def test_sealed_subclasses_indexed():
    names = all_names(SEALED)
    for expected in ["Result", "Success", "Failure", "Loading", "Shape", "Circle", "Square"]:
        assert expected in names, f"Missing sealed member: {expected}"


def test_sealed_subclass_scoped_to_parent():
    syms = {s.name: s for s in parse_file(SEALED, "Test.kt", "kotlin")}
    assert syms["Success"].kind == "class"
    assert syms["Loading"].kind == "class"


# ---------------------------------------------------------------------------
# 6. Interfaces with default implementations
# ---------------------------------------------------------------------------

INTERFACE = """\
interface Repository<T> {
    fun findById(id: Long): T?
    fun findAll(): List<T>
    fun save(entity: T): T
    fun count(): Int = 0
}
"""


def test_interface_indexed():
    assert "Repository" in all_names(INTERFACE)


def test_interface_methods_indexed():
    names = all_names(INTERFACE)
    for m in ["findById", "findAll", "save", "count"]:
        assert m in names, f"Interface method missing: {m}"


# ---------------------------------------------------------------------------
# 7. Top-level functions — all modifiers
# ---------------------------------------------------------------------------

FUNCTIONS = """\
fun plain(x: Int): Int = x
suspend fun suspended(id: Long): String = ""
inline fun <reified T> reified(block: () -> T): T = block()
tailrec fun factorial(n: Int, acc: Int = 1): Int = if (n <= 1) acc else factorial(n - 1, n * acc)
operator fun String.times(n: Int): String = repeat(n)
infix fun Int.add(other: Int): Int = this + other
"""


def test_top_level_functions_indexed():
    names = all_names(FUNCTIONS)
    for fn in ["plain", "suspended", "reified", "factorial", "times", "add"]:
        assert fn in names, f"Function missing: {fn}"


def test_top_level_function_kind():
    syms = sym_map(FUNCTIONS)
    for fn in ["plain", "suspended", "reified", "factorial"]:
        assert syms[fn].kind == "function", f"{fn} should be kind=function"


# ---------------------------------------------------------------------------
# 8. Extension functions — receiver type not confused with name
# ---------------------------------------------------------------------------

EXTENSIONS = """\
fun String.toTitleCase(): String = split(" ").joinToString(" ") { it.replaceFirstChar(String::uppercase) }
fun List<String>.containsIgnoreCase(target: String): Boolean = any { it.equals(target, ignoreCase = true) }
fun Int.isEven(): Boolean = this % 2 == 0
suspend fun Flow<Int>.sumAll(): Int = fold(0) { acc, v -> acc + v }
"""


def test_extension_functions_indexed_by_method_name():
    names = all_names(EXTENSIONS)
    assert "toTitleCase" in names
    assert "containsIgnoreCase" in names
    assert "isEven" in names
    assert "sumAll" in names


def test_extension_functions_are_kind_function():
    syms = sym_map(EXTENSIONS)
    for fn in ["toTitleCase", "containsIgnoreCase", "isEven", "sumAll"]:
        assert syms[fn].kind == "function"


# ---------------------------------------------------------------------------
# 9. Methods inside classes — scoping and kind
# ---------------------------------------------------------------------------

METHODS = """\
class Service {
    fun publicMethod(): String = ""
    private fun privateMethod() {}
    protected open fun openMethod() {}
    override fun toString(): String = "Service"
    suspend fun asyncMethod(): Int = 0
}
"""


def test_class_methods_indexed():
    names = all_names(METHODS)
    for m in ["publicMethod", "privateMethod", "openMethod", "toString", "asyncMethod"]:
        assert m in names


def test_class_methods_are_kind_method():
    syms = sym_map(METHODS)
    for m in ["publicMethod", "privateMethod", "openMethod"]:
        assert syms[m].kind == "method", f"{m} should be kind=method"


def test_class_methods_scoped_to_parent():
    syms = {s.name: s for s in parse_file(METHODS, "Test.kt", "kotlin")}
    assert "Service" in syms["publicMethod"].qualified_name


# ---------------------------------------------------------------------------
# 10. Inner / nested classes
# ---------------------------------------------------------------------------

NESTED = """\
class Outer {
    inner class Inner {
        fun innerMethod() {}
    }
    class Nested {
        fun nestedMethod() {}
    }
}
"""


def test_nested_classes_indexed():
    names = all_names(NESTED)
    assert "Inner" in names
    assert "Nested" in names


def test_nested_class_methods():
    names = all_names(NESTED)
    assert "innerMethod" in names
    assert "nestedMethod" in names


def test_inner_class_scoped_to_outer():
    syms = {s.name: s for s in parse_file(NESTED, "Test.kt", "kotlin")}
    assert "Outer" in syms["Inner"].qualified_name


# ---------------------------------------------------------------------------
# 11. Type aliases — top-level and nested
# ---------------------------------------------------------------------------

TYPE_ALIASES = """\
typealias Callback<T> = (T) -> Unit
typealias StringMap = Map<String, Any>
typealias Handler = suspend (String) -> Boolean

class Container {
    typealias Items = List<String>
    fun getItems(): Items = emptyList()
}
"""


def test_top_level_type_aliases():
    names = all_names(TYPE_ALIASES)
    assert "Callback" in names
    assert "StringMap" in names
    assert "Handler" in names


def test_nested_type_alias():
    names = all_names(TYPE_ALIASES)
    assert "Items" in names


def test_type_alias_kind():
    syms = sym_map(TYPE_ALIASES)
    assert syms["Callback"].kind == "type"
    assert syms["Items"].kind == "type"


# ---------------------------------------------------------------------------
# 12. Top-level constants and properties
# ---------------------------------------------------------------------------

CONSTANTS = """\
const val MAX_RETRY = 3
const val BASE_URL = "https://example.com"
val GLOBAL_TAG = "app"
val CONFIG_FILE = "config.json"
var lowerCaseVar = "should-be-ignored"
val alsoLower = 42
"""


def test_const_val_extracted():
    names = all_names(CONSTANTS)
    assert "MAX_RETRY" in names
    assert "BASE_URL" in names


def test_upper_case_val_extracted():
    names = all_names(CONSTANTS)
    assert "GLOBAL_TAG" in names
    assert "CONFIG_FILE" in names


def test_lower_case_val_not_extracted():
    names = all_names(CONSTANTS)
    assert "lowerCaseVar" not in names
    assert "alsoLower" not in names


def test_constant_kind():
    syms = sym_map(CONSTANTS)
    assert syms["MAX_RETRY"].kind == "constant"
    assert syms["GLOBAL_TAG"].kind == "constant"


# ---------------------------------------------------------------------------
# 13. Enum class — class indexed, methods inside indexed
# ---------------------------------------------------------------------------

ENUM = """\
enum class Direction(val degrees: Int) {
    NORTH(0), EAST(90), SOUTH(180), WEST(270);

    fun opposite(): Direction = when (this) {
        NORTH -> SOUTH
        SOUTH -> NORTH
        EAST -> WEST
        WEST -> EAST
    }

    companion object {
        fun fromDegrees(deg: Int): Direction = values().first { it.degrees == deg }
        const val DEFAULT_DIRECTION = "NORTH"
    }
}
"""


def test_enum_class_indexed():
    assert "Direction" in all_names(ENUM)


def test_enum_method_indexed():
    assert "opposite" in all_names(ENUM)


def test_enum_companion_method_indexed():
    assert "fromDegrees" in all_names(ENUM)


def test_enum_companion_constant_indexed():
    assert "DEFAULT_DIRECTION" in all_names(ENUM)


# ---------------------------------------------------------------------------
# 14. Annotations on declarations
# ---------------------------------------------------------------------------

ANNOTATIONS = """\
@Suppress("unused")
class AnnotatedClass {
    @Deprecated("Use newFun instead")
    fun oldFun(): String = ""

    @JvmStatic
    @Throws(Exception::class)
    fun staticFun(): Unit {}
}
"""


def test_annotated_class_indexed():
    assert "AnnotatedClass" in all_names(ANNOTATIONS)


def test_annotated_methods_indexed():
    names = all_names(ANNOTATIONS)
    assert "oldFun" in names
    assert "staticFun" in names


def test_decorators_extracted():
    syms = sym_map(ANNOTATIONS)
    assert any("Suppress" in d for d in syms["AnnotatedClass"].decorators)
    assert any("Deprecated" in d for d in syms["oldFun"].decorators)


# ---------------------------------------------------------------------------
# 15. Kotlin 2.x language features
# ---------------------------------------------------------------------------

GUARD_WHEN = """\
fun classify(value: Any): String = when (value) {
    is String if value.isNotEmpty() -> "non-empty string"
    is String -> "empty string"
    is Int if value > 0 -> "positive int"
    is Int -> "non-positive int"
    else -> "other"
}
"""

GUARD_WHEN_NO_GUARD = """\
fun classify(value: Any): String = when (value) {
    is String -> "string"
    is Int -> "int"
    else -> "other"
}
"""


@pytest.mark.skip(
    reason="Guard conditions (`is T if cond`) produce has_error=True in tree-sitter-kotlin "
    "grammar. Update when the grammar supports Kotlin 2.2 guard syntax."
)
def test_guard_conditions_in_when_parsed():
    """Guard conditions (stable in Kotlin 2.2) don't break function extraction."""
    assert "classify" in all_names(GUARD_WHEN)


def test_plain_when_without_guards_parsed():
    """Standard when expressions (without guards) are correctly indexed."""
    assert "classify" in all_names(GUARD_WHEN_NO_GUARD)


MULTI_DOLLAR = chr(10).join([
    'val template = "$$name is the variable"',
    'fun buildQuery(table: String): String = "$$SELECT * FROM ${table}"',
    "",
])

def test_multi_dollar_interpolation_parsed():
    """Multi-dollar string interpolation (stable in Kotlin 2.2) doesn't break extraction."""
    names = all_names(MULTI_DOLLAR)
    assert "buildQuery" in names


CONTEXT_PARAMS = """\
context(logger: Logger)
fun doWork(input: String): String {
    return input.trim()
}

context(db: Database, logger: Logger)
fun fetchUser(id: Long): String = ""
"""


def test_context_parameters_function_still_indexed():
    """context() prefix (Kotlin 2.2 preview) parses as call_expression + function_declaration.
    The function must still be indexed correctly."""
    names = all_names(CONTEXT_PARAMS)
    assert "doWork" in names
    assert "fetchUser" in names


VALUE_CLASS = """\
@JvmInline
value class UserId(val raw: Long)

@JvmInline
value class Email(val address: String) {
    fun isValid(): Boolean = address.contains("@")
}
"""


def test_value_class_indexed():
    names = all_names(VALUE_CLASS)
    assert "UserId" in names
    assert "Email" in names


def test_value_class_methods_indexed():
    assert "isValid" in all_names(VALUE_CLASS)


FUN_INTERFACE = """\
fun interface EventHandler {
    fun handle(event: String): Boolean
}

fun interface AsyncTransformer<T, R> {
    suspend fun transform(input: T): R
}
"""


@pytest.mark.skip(
    reason="fun interface produces has_error=True in tree-sitter-kotlin grammar; "
    "update this test when the grammar is fixed."
)
def test_fun_interface_indexed():
    names = all_names(FUN_INTERFACE)
    assert "EventHandler" in names
    assert "AsyncTransformer" in names


@pytest.mark.skip(reason="blocked by fun interface grammar limitation")
def test_fun_interface_method_indexed():
    names = all_names(FUN_INTERFACE)
    assert "handle" in names
    assert "transform" in names


DATA_OBJECT = """\
data object None
data object EmptyState {
    fun describe(): String = "empty"
}
"""


def test_data_object_indexed():
    names = all_names(DATA_OBJECT)
    assert "None" in names
    assert "EmptyState" in names


def test_data_object_method_indexed():
    assert "describe" in all_names(DATA_OBJECT)


# ---------------------------------------------------------------------------
# 16. Real-world Android ViewModel pattern
# ---------------------------------------------------------------------------

VIEWMODEL = """\
sealed class UiState {
    object Loading : UiState()
    data class Success(val items: List<String>) : UiState()
    data class Error(val message: String) : UiState()
}

class MainViewModel : ViewModel() {
    private val _uiState = MutableStateFlow<UiState>(UiState.Loading)
    val UI_STATE_KEY = "ui_state"

    fun loadData(userId: Long) {}

    suspend fun refresh() {}

    private fun handleError(e: Exception) {}

    companion object {
        const val TAG = "MainViewModel"
        fun create(): MainViewModel = MainViewModel()
    }
}
"""


def test_android_viewmodel_classes():
    names = all_names(VIEWMODEL)
    assert "UiState" in names
    assert "MainViewModel" in names
    assert "Loading" in names
    assert "Success" in names
    assert "Error" in names


def test_android_viewmodel_methods():
    names = all_names(VIEWMODEL)
    assert "loadData" in names
    assert "refresh" in names
    assert "handleError" in names


def test_android_viewmodel_companion():
    names = all_names(VIEWMODEL)
    assert "Companion" in names
    assert "TAG" in names
    assert "create" in names


def test_android_viewmodel_companion_constant():
    """TAG is a const val inside a companion object — must be indexed."""
    names = all_names(VIEWMODEL)
    assert "TAG" in names


def test_android_viewmodel_class_property_not_extracted():
    """val UI_STATE_KEY inside a regular class body is not extracted (not static scope)."""
    names = all_names(VIEWMODEL)
    assert "UI_STATE_KEY" not in names


# ---------------------------------------------------------------------------
# 17. Signature and docstring extraction
# ---------------------------------------------------------------------------

DOCSTRINGS = """\
/**
 * Fetches user data from the remote API.
 * @param id the user identifier
 */
fun fetchUser(id: Long): String = ""

// Single-line comment before function
fun singleLine(): Unit {}
"""


def test_kdoc_extracted_as_docstring():
    syms = sym_map(DOCSTRINGS)
    doc = syms["fetchUser"].docstring or ""
    assert "Fetches user data" in doc


def test_function_signature_captured():
    syms = sym_map(DOCSTRINGS)
    sig = syms["fetchUser"].signature or ""
    assert "fetchUser" in sig
    assert "Long" in sig


# ---------------------------------------------------------------------------
# 18. Empty / edge cases
# ---------------------------------------------------------------------------

def test_empty_file_returns_no_symbols():
    assert parse_file("", "empty.kt", "kotlin") == []


def test_package_and_imports_only():
    source = """\
package com.example.app

import android.os.Bundle
import androidx.lifecycle.ViewModel
"""
    assert parse_file(source, "Imports.kt", "kotlin") == []


def test_comments_only():
    source = """\
// This file is intentionally left blank.
/* Multi-line comment */
"""
    assert parse_file(source, "Comments.kt", "kotlin") == []
