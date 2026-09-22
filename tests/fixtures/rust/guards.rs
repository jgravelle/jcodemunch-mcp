//! Shapes where the extractor could FABRICATE a name Rust does not bind.
//!
//! ⚠⚠ This file exists for the `extra` bucket, which must stay at zero. The
//! Racket harness learned the hard way that a fabrication is worse than a gap:
//! an LLM handed a name that does not exist repeats the error, where one handed
//! an incomplete index just reads the file.
//!
//! ⚠ The `#[cfg]`-paired inner function is the shape that broke the measurement
//! before the harness existed: an oracle that does not walk function bodies
//! reports every one of these as invented by the extractor, inverting the gate.
//! ripgrep's `pathutil.rs` uses it eight times in one file.

/// Two inner `imp`s under opposite cfgs. BOTH are real definitions.
pub fn platform_dependent(path: &str) -> bool {
    #[cfg(windows)]
    fn imp(path: &str) -> bool {
        path.contains('\\')
    }

    #[cfg(not(windows))]
    fn imp(path: &str) -> bool {
        path.starts_with('/')
    }

    imp(path)
}

/// A macro INVOCATION defines nothing here. `vec!` and `println!` must not
/// become symbols, and neither must the identifiers inside them.
pub fn calls_macros() -> Vec<u32> {
    let v = vec![1, 2, 3];
    println!("{v:?}");
    v
}

/// A macro DEFINITION binds `shout`. Its body mentions `inner_thing`, which is
/// NOT a definition at this site -- it exists only after expansion, which we do
/// not perform.
#[macro_export]
macro_rules! shout {
    ($e:expr) => {{
        fn inner_thing() -> u32 {
            0
        }
        let _ = inner_thing();
        $e
    }};
}

/// A closure bound to a `let` is a VALUE, not a definition. `helper` must not
/// be indexed as a function.
pub fn holds_a_closure() -> u32 {
    let helper = |x: u32| x * 2;
    helper(21)
}

/// A struct's named field IS a member as of #786; an enum variant is still not
/// a standalone definition. `depth` is a symbol, `Alpha` and `Beta` are not.
pub struct HasFields {
    pub depth: usize,
}

/// `Gamma` carries NAMED fields, which the grammar and `syn` both spell the
/// same way a struct's are -- so `shade` is what a variant-field channel
/// would wrongly adopt, on either side. Neither side emits it, and without
/// this shape nothing in the corpus could tell.
pub enum HasVariants {
    Alpha,
    Beta(u32),
    Gamma { shade: u8 },
}

/// A trait implementation for a foreign type binds nothing new at module level.
impl std::ops::Add for HasFields {
    type Output = HasFields;

    fn add(self, other: HasFields) -> HasFields {
        HasFields { depth: self.depth + other.depth }
    }
}

/// `const` inside a function body IS a real definition, and is currently one of
/// the reported gaps. Kept here so the gap is visible in the fixture set rather
/// than only in the corpus run.
pub fn holds_consts() -> usize {
    const LOCAL_LIMIT: usize = 7;
    LOCAL_LIMIT
}
