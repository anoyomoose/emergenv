# Expansion

*emergenv* can compute values at build time: substitute one variable into
another, apply text transformations, and evaluate integer arithmetic. This
document specifies **exactly** what is and isn't supported, case by case.

This is a **defined subset** of shell-style expansion, implemented in pure Python.
It is deliberately **not bash**: there is **no command execution, ever** (no
`$(...)`, no backticks), and several bash behaviours are intentionally changed or
dropped where they are footguns. Divergences are called out throughout and
summarised at the end.

## Philosophy

- **Literal by default.** A plain `KEY=value` line is never touched - its value is
  emitted byte-for-byte. This matters because values are usually secrets, and
  secrets routinely contain `$`, `{`, backticks, etc. Expansion only happens on
  lines you explicitly mark.
- **Fail loud.** Referencing something undefined is an error, not a silent empty
  string. Use an explicit default form when you mean "or fall back".
- **No execution.** Expansion can read and reshape values; it can never run a
  command.

## Marking a value for expansion

Two assignment markers opt a line into expansion:

| Form        | Namespace it can reference                                            |
|-------------|----------------------------------------------------------------------|
| `$KEY=…`    | Other built keys only. The host environment is **invisible**.        |
| `%KEY=…`    | Other built keys, **plus** the host environment as a fallback.       |

- Plain `KEY=value` is **never** expanded.
- For `%KEY=`, a **locally defined key always wins** over an environment variable
  of the same name; the environment only fills in names that aren't defined in the
  build. `%` makes a build depend on the *build host's* environment, so it is not
  reproducible across machines - use it deliberately (e.g. injecting `${HOSTNAME}`).
- Under `$KEY=`, a name that exists *only* in the environment counts as **unset**
  (→ error). Only `%` consults the environment.

Both markers compose with `export`:

```
export $DB_URL=postgres://${DB_USER}@${DB_HOST}/db
```

The markers also **travel** through `@include` and `@key=`. If a fragment defines
`$KEY=…` and a parent pulls it with `@KEY=fragment`, the parent receives `$KEY=…`
(still computed), evaluated in the parent's context.

> **Gotcha:** `@KEY=fragment` carries the *template* but **not its dependencies**.
> If `$DB_URL=${DB_HOST}/db` is pulled by keyref into a place where `DB_HOST`
> isn't defined above it, you get an "undefined variable" error. Pull the whole
> fragment with `@include`, or make sure the referenced names travel too.

## Evaluation model

Expansion is the **last** transformation before output (and before `--bare`
stripping). The build pipeline is:

1. **Assemble** - expand `@include` / `@key=` recursively into one ordered stream
   of lines. `$`/`%` markers are preserved verbatim.
2. **Evaluate** - a single pass, top to bottom, maintaining a **live namespace**.
   Every assignment encountered (plain, computed, keyref-resolved, `export`ed)
   updates the namespace as it is read. A `$`/`%` line is evaluated against the
   namespace **as it stands at that line**, and its result then enters the
   namespace for later lines.
3. **Last-wins** - duplicate assignments are resolved (earlier ones commented out).
4. **Render** - `# FROM:` provenance and `# COMPUTED:` lines are emitted; `--bare`
   strips all comments, leaving only the final assignments.

Key consequences of the live, sequential, single-pass model:

- **Define before use.** A line can only reference names defined *above* it.
  A forward reference is undefined → error. This is why all the context an
  expression names must be "crystallised" earlier in the build - e.g.
  `$DB_PORT=$(( BASE_PORT + DB_OFFSET ))` requires both `BASE_PORT` and
  `DB_OFFSET` to already be set.
- **Cycles are impossible.** A value can only use prior values, so nothing can
  loop. Re-assignment is fine and uses the value live at each point:

  ```
  PORT=1
  $A=${PORT}     # A = 1
  PORT=2
  $B=${PORT}     # B = 2  (final PORT is 2; A stays 1)
  ```

  Self-reference uses the previous value: `$X=${X}-v2` appends to the old `X`.
- **No re-expansion of substituted values.** Expansion applies to the *template
  as written*. A value substituted in is inserted **literally** and never
  re-scanned. If `${PASS}` resolves to `a${B}c`, the `${B}` stays literal text;
  it is not expanded. This is a security property: a secret cannot inject
  expansion (or, in glob positions, wildcard) syntax. See [Nesting](#nesting).

### Rendering of a computed line

A computed line keeps its original directive as a comment, with the result below:

```
# COMPUTED: $DB_URL=postgres://${DB_USER}:${DB_PASS}@${DB_HOST}:${DB_PORT}/${DB_NAME}
DB_URL=postgres://app:s3cr3t@db.internal:5432/appdb
```

With `--bare`, only `DB_URL=postgres://app:s3cr3t@db.internal:5432/appdb` remains.
`export $KEY=…` renders as `export KEY=result`.

## References: `${VAR}`

- References are **always** brace-delimited: `${VAR}`. Bare `$VAR` is **not**
  supported (it is literal text - see [Escaping](#escaping-and-literals)).
- A variable name is `[A-Za-z_][A-Za-z0-9_]*`.
- **Unset → error.** `${VAR}` where `VAR` was never defined aborts the build.
  (This is a deliberate divergence from bash, which yields empty.)
- **Defined but empty → empty string**, no error.

## Parameter expansion forms

In every form below, `VAR` unset (with no default/alternate covering it) is an
**error**. "Empty" means defined as `""`. The colon variants treat *empty the same
as unset*; the colon-less variants act only on *unset*.

### Defaults, required, alternate

| Form            | Result                                                              |
|-----------------|--------------------------------------------------------------------|
| `${VAR:-word}`  | `VAR` if set and non-empty, otherwise `word`.                      |
| `${VAR-word}`   | `VAR` if set (even if empty), otherwise `word`.                    |
| `${VAR:?word}`  | `VAR` if set and non-empty, otherwise **abort** with message `word`.|
| `${VAR?word}`   | `VAR` if set (even if empty), otherwise **abort** with message `word`.|
| `${VAR:+word}`  | `word` if `VAR` is set and non-empty, otherwise empty.             |
| `${VAR+word}`   | `word` if `VAR` is set (even if empty), otherwise empty.           |

`word` is itself a template: it may contain literal text and nested `${…}`
references (see [Nesting](#nesting)), e.g. `${PORT:-${DEFAULT_PORT}}`.

### Length

| Form       | Result                                                          |
|------------|----------------------------------------------------------------|
| `${#VAR}`  | Length of `VAR` in **Unicode characters** (code points).       |

`${#VAR}` on an unset `VAR` is an error.

### Substring

| Form                  | Result                                                  |
|-----------------------|--------------------------------------------------------|
| `${VAR:offset}`       | From `offset` to the end.                              |
| `${VAR:offset:length}`| `length` characters starting at `offset`.             |

- `offset` and `length` are integers: either an integer literal or a `${ref}`
  that resolves to an integer. **No arithmetic** is allowed here - compute it in a
  prior `$(( … ))` line and reference the result.
- Indices are in **Unicode characters**.
- A **negative** `offset` counts from the end and **requires a leading space** to
  disambiguate from `:-`: `${VAR: -3}` (last three characters). A negative
  `length` sets the end position that many characters from the end:
  `${VAR:0:-2}` (all but the last two).
- Out-of-range values clamp: an `offset` past the end yields empty; an over-long
  `length` truncates; if the computed end is before the start, the result is empty.
- `${VAR:…}` on an unset `VAR` is an error.

### Prefix / suffix removal

The pattern is a [glob](#glob-patterns).

| Form           | Result                                                       |
|----------------|-------------------------------------------------------------|
| `${VAR#pat}`   | Remove the **shortest** leading match of `pat`.             |
| `${VAR##pat}`  | Remove the **longest** leading match of `pat`.              |
| `${VAR%pat}`   | Remove the **shortest** trailing match of `pat`.            |
| `${VAR%%pat}`  | Remove the **longest** trailing match of `pat`.             |

No match leaves the value unchanged. `${VAR#…}` etc. on an unset `VAR` is an error.

### Search and replace

The pattern is a [glob](#glob-patterns); the replacement is literal text plus
optional nested `${…}`.

| Form               | Result                                                   |
|--------------------|---------------------------------------------------------|
| `${VAR/pat/str}`   | Replace the **first** match of `pat` with `str`.        |
| `${VAR//pat/str}`  | Replace **all** matches.                                |
| `${VAR/#pat/str}`  | Replace only if `pat` matches at the **start**.         |
| `${VAR/%pat/str}`  | Replace only if `pat` matches at the **end**.           |
| `${VAR/pat}`       | Delete the first match (empty replacement).             |
| `${VAR//pat}`      | Delete all matches.                                     |

- `//` is the "replace all" selector - it is **not** an escape for `/`. To match a
  literal `/`, escape it inside the pattern: `${PATH//\//_}` replaces every `/`
  with `_`.
- A literal `/` in the *replacement* needs no escaping.
- `${VAR/…}` on an unset `VAR` is an error.

### Case modification

| Form         | Result                                              |
|--------------|-----------------------------------------------------|
| `${VAR^}`    | Uppercase the **first** character.                  |
| `${VAR^^}`   | Uppercase **all** characters.                       |
| `${VAR,}`    | Lowercase the **first** character.                  |
| `${VAR,,}`   | Lowercase **all** characters.                       |

Case mapping uses Python's Unicode rules (not the C locale). The pattern-filtered
bash forms (`${VAR^^[set]}`) are **not** supported. Unset `VAR` is an error.

## Glob patterns

Prefix/suffix removal and search/replace match with globs (not regexes):

| Token     | Matches                                                          |
|-----------|-----------------------------------------------------------------|
| `*`       | Any run of characters, including none.                          |
| `?`       | Exactly one character.                                          |
| `[abc]`   | Any one of the listed characters.                               |
| `[a-z]`   | Any one character in the range.                                 |
| `[!abc]`  | Any one character **not** listed.                               |
| `\x`      | The literal character `x` (escapes a metacharacter).            |

- Backslash escaping is active **only inside glob patterns**: `\*`, `\?`, `\[`,
  `\/`, `\}`, `\\`. Everywhere else, backslash is a literal character (so a
  password containing `\n` is untouched).
- A glob metacharacter that arrives via a nested `${…}` is **literal**, never
  active (see [Nesting](#nesting)). Only metacharacters you type directly match.

## Arithmetic: `$(( … ))`

```
$DB_PORT=$(( BASE_PORT + DB_OFFSET ))
$REPLICAS=$(( (CORES - 1) * 2 ))
```

- Inside `$(( … ))`, variables are written as **bare names** - a `$` anywhere
  inside an arithmetic expression is **illegal**. Use parentheses `( )` for
  grouping.
- Bare names resolve from the namespace and must hold integers. An **unset** name
  is an error (not `0` as in bash).
- Everything is **integer**. A non-integer operand (e.g. a value of `1.5` or
  `abc`) is an error.

### Operators

All operators are side-effect free; assignment-like and sequencing operators are
**not** supported.

| Category    | Operators                                  |
|-------------|--------------------------------------------|
| Unary       | `+`  `-`  `~` (bitwise not)  `!` (logical not) |
| Arithmetic  | `+`  `-`  `*`  `/`  `%`  `**`               |
| Bitwise     | `&`  `\|`  `^`  `<<`  `>>`                  |
| Comparison  | `==`  `!=`  `<`  `<=`  `>`  `>=` (yield `1`/`0`) |
| Logical     | `&&`  `\|\|` (yield `1`/`0`)                |
| Ternary     | `cond ? a : b`                             |
| Grouping    | `( … )`                                    |

**Not supported:** `=`, `+=`, `-=`, … (assignment), `++`, `--`, and `,` (comma).

### Integer literals

Parsed with explicit prefixes; the ambiguous bash leading-zero form is rejected:

| Literal   | Value                                                            |
|-----------|-----------------------------------------------------------------|
| `42`      | 42 (decimal)                                                    |
| `0x1F`    | 31 (hex)                                                        |
| `0o17`    | 15 (octal)                                                      |
| `0b101`   | 5 (binary)                                                      |
| `010`     | **Error** - ambiguous. Write `0o10` for 8, or `10` for ten.    |

The same rule applies to variable *values* used in arithmetic: a value of `010`
is an ambiguous-literal error, never silently `8`. bash's `base#n` syntax is not
supported.

### Semantics

- `/` and `%` **truncate toward zero** (so `-7 / 2 == -3`, `-7 % 2 == -1`),
  matching C and bash.
- Division or modulo by zero is an error.
- A negative exponent (`2 ** -1`) is an error (the result would not be an integer).

## Escaping and literals

Within a `$`/`%` value, only three sequences are special:

| Sequence | Meaning                          |
|----------|----------------------------------|
| `${`     | Start of a reference.            |
| `$((`    | Start of an arithmetic block.    |
| `$$`     | A literal `$`.                   |

Any other `$` is a literal `$` (so ordinary `$`-laden passwords need no escaping).
To write a literal `${` or `$((`, prefix with `$$`: `$${` → `${`, `$$((` → `$((`.

- Escaping rules apply **only** to `$`/`%` lines. A plain `KEY=value` is fully
  literal and never needs escaping.
- There is **no quote removal and no word splitting**. Quotes are ordinary
  characters: `$URL="${A}"` produces `URL="resolved"` with the quotes intact.

## Nesting

`${…}` references may nest in **any** sub-part - default/alternate word, glob
pattern, replacement string, and substring offset - and are resolved
**inner-first**. The text a nested reference produces is always **literal**:

- It is not re-scanned for further `${…}` / `$(( … ))`.
- In a glob position it is matched **literally** - wildcard characters in a
  substituted value do not become active.

Examples:

```
$OUT=${A:-${B}}            # A unset -> use B's value, verbatim
$OUT=${URL/${OLD}/${NEW}}  # replace OLD's value with NEW's value, both literal
$OUT=${PATH#${PREFIX}}     # strip PREFIX's value as a literal prefix
```

If `B`'s value happened to be the text `${C}`, the first example yields the literal
`${C}` - it does **not** chain through to `C`. (No re-expansion; see the
[evaluation model](#evaluation-model).)

## Errors

Any of the following **aborts the build** (non-zero exit), reported with the
offending file/source for context:

- A reference to an unset variable with no default/alternate covering it.
- `${VAR:?word}` / `${VAR?word}` triggering, surfacing `word`.
- An unterminated `${` or `$(( `.
- A `$` inside `$(( … ))`.
- A non-integer operand, an ambiguous integer literal (`010`), division/modulo by
  zero, or a negative exponent in arithmetic.
- A non-integer substring offset or length.
- An invalid variable name in `${…}`.

## Not supported (and why)

| Feature                                          | Status / reason                                   |
|--------------------------------------------------|---------------------------------------------------|
| Command substitution `$(…)`, backticks           | **Never** - would be arbitrary code execution.    |
| Process substitution `<(…)`                       | Never - execution.                                |
| Bare `$VAR` (unbraced)                            | Use `${VAR}` for clarity and unambiguous parsing. |
| `${VAR:=word}` / `${VAR=word}` (assign-default)  | Excluded - mutates the namespace as a side effect.|
| `${!VAR}` (indirection), `${!prefix*}`           | Excluded - hidden indirection.                    |
| Arrays (`${arr[@]}`, `${arr[0]}`, …)             | Env values are scalars.                           |
| Case-mod pattern forms (`${VAR^^[set]}`)         | Excluded - obscure; plain `^ ^^ , ,,` only.       |
| Implicit `0NNN` octal, `base#n`                   | `0NNN` is an ambiguity error; use `0o…`.          |
| Arithmetic side effects (`=`, `++`, `--`, `,`)   | Excluded - keep expressions pure.                 |
| Re-expansion of substituted values               | Single pass; substituted text is literal (safety).|
| Word splitting, quote removal                    | Quotes are literal; values are taken whole.       |
| Locale-dependent behaviour                        | Length/case/indices use Python Unicode semantics. |

## Differences from bash (summary)

Even within the supported subset, emergenv intentionally differs:

- **Unset `${VAR}` is an error** (bash: empty).
- **Unset name in `$(( … ))` is an error** (bash: `0`); `$` is illegal inside
  arithmetic (bash: allows `$VAR`).
- **`010` is an error** (bash: octal `8`); octal must be written `0o10`.
- **Length, substring indices and case mapping are Unicode**, not locale/byte
  based.
- **No word splitting or quote removal.**
- Several constructs are simply absent (see the table above).
</content>
