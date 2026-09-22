//! Ground truth for Rust symbol extraction: Rust's own parser.
//!
//! Emits one record per DEFINITION with name, kind and the 1-based line of the
//! IDENTIFIER.
//!
//! ⚠⚠ Built on `syn::visit::Visit`, NOT a hand-rolled walk, and that is a
//! correctness decision rather than a style one. A hand-rolled walker only sees
//! the places its author remembered to look, and every place it forgets makes
//! the EXTRACTOR look wrong: a definition the oracle cannot reach is scored as
//! a fabrication. Two rounds of that happened here before this rewrite --
//! first function bodies (35 phantom "extras"), then `const`s inside a `for`
//! body and a `match` arm (2 more). `Visit` recurses through expressions,
//! blocks, arms and closures by default, so the blind spots have to be added
//! deliberately instead of arrived at by omission.
//!
//! ⚠⚠ The IDENTIFIER's span, never the item's. `syn`'s `Item::span()` starts at
//! the first doc comment or outer attribute, so an item's span line is where
//! its DOCUMENTATION begins. Measuring against that scored jCodeMunch at 40.4%
//! when the real figure was 95.4%, and every delta was one-sided -- jcm was
//! never earlier, which is the signature of an oracle artifact rather than an
//! extractor bug.
//!
//! ⚠⚠ THIS IS A PARSER, NOT AN EXPANDER, and that is the honest ceiling of the
//! whole harness. Racket's oracle expands, so it sees macro-introduced names.
//! `syn` does not expand, so an item produced by a `macro_rules!` invocation is
//! invisible to BOTH sides. Such names are not scored here, in either
//! direction, and no bucket should be read as covering them.
use proc_macro2::Span;
use std::collections::BTreeSet;
use quote::ToTokens;
use syn::visit::Visit;

#[derive(PartialEq, Eq, PartialOrd, Ord)]
struct Def {
    file: String,
    name: String,
    qual: String,
    kind: &'static str,
    line: usize,
}

fn line_of(s: Span) -> usize {
    s.start().line
}

struct Collector {
    file: String,
    out: BTreeSet<Def>,
    /// The enclosing scopes currently open: `impl` / `trait` owner types AND
    /// the functions whose bodies we are inside.
    ///
    /// ⚠⚠ Name alone is not a definition's identity and a set of names cannot
    /// count. `crates/core/flags/defs.rs` defines `is_switch` 108 times, once
    /// per flag; a comparison keyed on bare names sees ONE `is_switch` on each
    /// side and calls it a match. `qual` is what makes those 108 distinct, and
    /// what lets the harness notice if 107 of them stop being extracted.
    scope: Vec<String>,
}

impl Collector {
    fn push(&mut self, name: String, kind: &'static str, line: usize) {
        let qual = if self.scope.is_empty() {
            name.clone()
        } else {
            format!("{}.{}", self.scope.join("."), name)
        };
        self.out.insert(Def { file: self.file.clone(), name, qual, kind, line });
    }

    /// Every NAMED field of a struct or union (#786).
    ///
    /// ⚠⚠ `Fields::Unnamed` is a tuple struct and its members have
    /// `ident: None` -- there is no name to emit, so both sides skip them by
    /// CONSTRUCTION rather than by a rule either had to be told. The
    /// tree-sitter grammar agrees: a tuple struct is an
    /// `ordered_field_declaration_list` with no `field_identifier` in it.
    ///
    /// ⚠ Called from the struct and union visitors and NOT from `visit_field`,
    /// which also fires for an enum VARIANT's fields. Reaching them through
    /// the container is what keeps variants out without a second predicate.
    fn push_named_fields(&mut self, fields: &syn::Fields) {
        if let syn::Fields::Named(named) = fields {
            for f in &named.named {
                if let Some(id) = &f.ident {
                    self.push(id.to_string(), "field", line_of(id.span()));
                }
            }
        }
    }
}

/// The type an `impl` block implements FOR.
///
/// ⚠⚠ `self_ty`, never the trait. In `impl Display for Foo` the methods belong
/// to `Foo`; `Display` is which contract they satisfy, not who owns them.
/// Keying on the trait puts every type's `fmt` in one bucket named `Display`,
/// which is the same collision one level over.
fn impl_owner(ty: &syn::Type) -> Option<String> {
    match ty {
        syn::Type::Path(p) => {
            let seg: Vec<String> =
                p.path.segments.iter().map(|s| s.ident.to_string()).collect();
            if seg.is_empty() { None } else { Some(seg.join("::")) }
        }
        syn::Type::Reference(r) => impl_owner(&r.elem),
        syn::Type::Group(g) => impl_owner(&g.elem),
        syn::Type::Paren(p) => impl_owner(&p.elem),
        syn::Type::TraitObject(t) => t.bounds.iter().find_map(|b| match b {
            syn::TypeParamBound::Trait(tb) => {
                tb.path.segments.last().map(|s| s.ident.to_string())
            }
            _ => None,
        }),
        // ⚠ Everything else -- `&[u8]`, `(u8, u8)`, `[T; 4]` -- has no path to
        // read, so it is rendered from its own tokens with the spaces removed.
        // Returning None here instead would file every such impl's members
        // under a bare name, which is the collision this whole field exists to
        // remove, and would put the oracle BELOW the extractor it scores.
        other => {
            let s = other.to_token_stream().to_string();
            let s: String = s.chars().filter(|c| !c.is_whitespace()).collect();
            if s.is_empty() { None } else { Some(s) }
        }
    }
}

impl<'ast> Visit<'ast> for Collector {
    /// ⚠ The function's own name is pushed before descending, so an item
    /// defined INSIDE a body is qualified by the body that holds it. Rust
    /// cannot name either one from outside, so neither spelling is "the"
    /// path -- but `try_resolve_binary.is_exe` says where the helper lives
    /// and a bare `is_exe` collides with every other one in the crate.
    fn visit_item_fn(&mut self, i: &'ast syn::ItemFn) {
        self.push(i.sig.ident.to_string(), "function", line_of(i.sig.ident.span()));
        self.scope.push(i.sig.ident.to_string());
        syn::visit::visit_item_fn(self, i);
        self.scope.pop();
    }

    /// ⚠⚠ The struct's own name is pushed onto `scope` around the descent, the
    /// way `visit_item_fn` does, and WITHOUT that the fields below come out
    /// qualified as bare names -- which is the collision `qual` exists to
    /// remove. Nothing else is emitted from inside a struct, so no pre-#786
    /// `qual` moves: regenerating the artifact only ADDS field rows.
    fn visit_item_struct(&mut self, i: &'ast syn::ItemStruct) {
        self.push(i.ident.to_string(), "struct", line_of(i.ident.span()));
        self.scope.push(i.ident.to_string());
        self.push_named_fields(&i.fields);
        syn::visit::visit_item_struct(self, i);
        self.scope.pop();
    }

    /// A union's members are the same `Field` nodes a struct's are, and
    /// tree-sitter spells them identically too. Excluding them would need a
    /// condition written against the word `union` for no reason anyone could
    /// state.
    fn visit_item_union(&mut self, i: &'ast syn::ItemUnion) {
        self.push(i.ident.to_string(), "union", line_of(i.ident.span()));
        self.scope.push(i.ident.to_string());
        for f in &i.fields.named {
            if let Some(id) = &f.ident {
                self.push(id.to_string(), "field", line_of(id.span()));
            }
        }
        syn::visit::visit_item_union(self, i);
        self.scope.pop();
    }

    fn visit_item_enum(&mut self, i: &'ast syn::ItemEnum) {
        self.push(i.ident.to_string(), "enum", line_of(i.ident.span()));
        syn::visit::visit_item_enum(self, i);
    }


    fn visit_item_trait(&mut self, i: &'ast syn::ItemTrait) {
        self.push(i.ident.to_string(), "trait", line_of(i.ident.span()));
        self.scope.push(i.ident.to_string());
        syn::visit::visit_item_trait(self, i);
        self.scope.pop();
    }

    /// ⚠ The impl block itself is NOT pushed as a definition. There is no
    /// `impl` a caller can name or import; it is a scope. jCodeMunch emits
    /// none either, so both sides agree, and emitting one here would score
    /// correct extraction as a miss.
    fn visit_item_impl(&mut self, i: &'ast syn::ItemImpl) {
        let owner = impl_owner(&i.self_ty);
        let pushed = owner.is_some();
        if let Some(o) = owner {
            self.scope.push(o);
        }
        syn::visit::visit_item_impl(self, i);
        if pushed {
            self.scope.pop();
        }
    }

    fn visit_item_type(&mut self, i: &'ast syn::ItemType) {
        self.push(i.ident.to_string(), "type", line_of(i.ident.span()));
        syn::visit::visit_item_type(self, i);
    }

    fn visit_item_const(&mut self, i: &'ast syn::ItemConst) {
        self.push(i.ident.to_string(), "constant", line_of(i.ident.span()));
        syn::visit::visit_item_const(self, i);
    }

    fn visit_item_static(&mut self, i: &'ast syn::ItemStatic) {
        self.push(i.ident.to_string(), "constant", line_of(i.ident.span()));
        syn::visit::visit_item_static(self, i);
    }

    fn visit_item_mod(&mut self, i: &'ast syn::ItemMod) {
        self.push(i.ident.to_string(), "module", line_of(i.ident.span()));
        syn::visit::visit_item_mod(self, i);
    }

    fn visit_item_macro(&mut self, i: &'ast syn::ItemMacro) {
        // `macro_rules! name { .. }` DEFINES. A macro CALL has no ident.
        if let Some(id) = &i.ident {
            self.push(id.to_string(), "macro", line_of(id.span()));
        }
        syn::visit::visit_item_macro(self, i);
    }

    fn visit_impl_item_fn(&mut self, i: &'ast syn::ImplItemFn) {
        self.push(i.sig.ident.to_string(), "method", line_of(i.sig.ident.span()));
        self.scope.push(i.sig.ident.to_string());
        syn::visit::visit_impl_item_fn(self, i);
        self.scope.pop();
    }

    fn visit_impl_item_const(&mut self, i: &'ast syn::ImplItemConst) {
        self.push(i.ident.to_string(), "constant", line_of(i.ident.span()));
        syn::visit::visit_impl_item_const(self, i);
    }

    fn visit_impl_item_type(&mut self, i: &'ast syn::ImplItemType) {
        self.push(i.ident.to_string(), "type", line_of(i.ident.span()));
        syn::visit::visit_impl_item_type(self, i);
    }

    /// ⚠ Covers BOTH halves of a trait method: one with a default body and one
    /// that is a signature only (`fn required(&self) -> u32;`). The second was
    /// a real extraction gap -- the API surface an implementor must provide was
    /// exactly the half jCodeMunch could not find.
    fn visit_trait_item_fn(&mut self, i: &'ast syn::TraitItemFn) {
        self.push(i.sig.ident.to_string(), "method", line_of(i.sig.ident.span()));
        self.scope.push(i.sig.ident.to_string());
        syn::visit::visit_trait_item_fn(self, i);
        self.scope.pop();
    }

    fn visit_trait_item_const(&mut self, i: &'ast syn::TraitItemConst) {
        self.push(i.ident.to_string(), "constant", line_of(i.ident.span()));
        syn::visit::visit_trait_item_const(self, i);
    }

    fn visit_trait_item_type(&mut self, i: &'ast syn::TraitItemType) {
        self.push(i.ident.to_string(), "type", line_of(i.ident.span()));
        syn::visit::visit_trait_item_type(self, i);
    }

    /// ⚠⚠ **This no longer omits struct fields, and the reason the old note
    /// gave was wrong twice over.** It said fields bind no name another module
    /// can reach -- a `pub` field is reached as `s.field` -- and that emitting
    /// them "would make the `extra` gate reject correct extraction", which was
    /// true only while the extractor emitted none. Once it does, the omission
    /// is what fails the gate on correct extraction, and the whole point of an
    /// oracle is that it can be wrong in a way that blames the thing it scores
    /// (#786). Named struct and union fields are emitted by their CONTAINERS,
    /// via `push_named_fields`.
    ///
    /// ⚠ Still deliberately NOT emitted here: an enum VARIANT's fields, which
    /// reach this visitor too. A variant is not a struct, variants themselves
    /// are omitted, and indexing a variant's fields while the variant is
    /// absent would be a half-answer. `let` bindings and closures stay out for
    /// the original reason, which does hold for them.
    ///
    /// Each omission is a decision recorded here; a hand-rolled walker records
    /// the same omissions as silence.
    fn visit_field(&mut self, i: &'ast syn::Field) {
        syn::visit::visit_field(self, i);
    }
}

fn main() {
    let root = std::env::args().nth(1).expect("usage: oracle <dir>");
    let root = std::path::PathBuf::from(root);
    let mut out = BTreeSet::new();
    let (mut parsed, mut failed) = (0usize, 0usize);
    let mut failed_files: Vec<String> = Vec::new();
    for e in walkdir::WalkDir::new(&root).into_iter().filter_map(|e| e.ok()) {
        let p = e.path();
        if p.extension().map(|x| x != "rs").unwrap_or(true) {
            continue;
        }
        if p.components().any(|c| c.as_os_str() == "target") {
            continue;
        }
        let rel = p
            .strip_prefix(&root)
            .unwrap()
            .to_string_lossy()
            .replace(std::path::MAIN_SEPARATOR, "/");
        let src = match std::fs::read_to_string(p) {
            Ok(s) => s,
            Err(_) => {
                failed += 1;
                failed_files.push(rel);
                continue;
            }
        };
        match syn::parse_file(&src) {
            Ok(f) => {
                parsed += 1;
                let mut c =
                    Collector { file: rel, out: BTreeSet::new(), scope: Vec::new() };
                c.visit_file(&f);
                out.extend(c.out);
            }
            Err(_) => {
                failed += 1;
                failed_files.push(rel);
            }
        }
    }
    let recs: Vec<_> = out
        .iter()
        .map(|d| {
            serde_json::json!({
                "file": d.file, "name": d.name, "qual": d.qual,
                "kind": d.kind, "line": d.line
            })
        })
        .collect();
    let doc = serde_json::json!({
        "files_parsed": parsed, "files_failed": failed,
        "failed_files": failed_files, "defs": recs,
    });
    println!("{}", serde_json::to_string(&doc).unwrap());
}
