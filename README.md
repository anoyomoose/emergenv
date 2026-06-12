# EMERGENV

**E**ncrypted, **Merg**ed **Env**ironment

*Perfect for people who need exactly this!*

## Purpose

Profile-based merging of environment files, encrypted with `age` and your SSH keys.

*emergenv* is built for **git-based deploys to dedicated servers** - the bare-metal,
VPS, and homelab world, not cloud platforms with a managed secret store. The encrypted
`.age` files live in the repo, just do a checkout, and `emergenv build` using the SSH
host key the server already has. No KMS, no secret-injection pipeline, no separate key
inventory - the secrets ride along in the repo and the box already holds the only key
it needs to decrypt.

At its simplest it's git-crypt for .env files - three commands. Everything else
(DRY composition, profiles, variable substitution, integer arithmetic) is opt-in when you
need it. See [Simplest possible use](#simplest-possible-use-just-encrypt-one-file).

## Contents

- [Purpose](#purpose)
- [Why emergenv?](#why-emergenv)
- [License](#license)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Deployment](#deployment)
- [Directory structure](#directory-structure)
- [What to commit](#what-to-commit)
- [Encryption](#encryption)
- [Decryption](#decryption)
- [Profiles](#profiles)
- [Syntax](#syntax)
- [Expansion](#expansion)
- [Base file](#base-file)
- [Resolution order](#resolution-order)
- [Command reference](#command-reference)
- [Recipes](#recipes)
- [Security](#security)
- [pyproject.toml integration](#pyprojecttoml-integration)

## Why emergenv?

Most secret tooling assumes the cloud: a managed KMS, a Vault cluster, or a platform
that injects env vars at runtime. The classic dedicated-server workflow looks nothing
like that - you use a triggered pull or you push to a bare git repo on the box itself 
and let a `post-receive` hook check out and deploy. None of the cloud machinery fits,
and you don't want it.

*emergenv* targets that case specifically:

- **No infrastructure.** It needs only the `age` binary and SSH keys that already exist
  on the machines involved. A server decrypts with its own host key; people decrypt with
  the keys in their `~/.ssh`.
- **Encrypted in the repo.** `.age` files are committed, so a diff shows *that* a secret
  changed without revealing *what*. Plaintext `.env` files stay out of git.
- **Composition, not just storage.** Unlike git-crypt or `sops` - which encrypt and decrypt
  blobs - *emergenv* merges fragments across profiles and targets via `@include` / `@<key>=`,
  with per-line `# FROM:` provenance so you can see where every value came from and what
  overrode it. If your base `.env` is now 200 lines and your `docker-compose.yml`
  is a copy/paste party rather than using `env_file`, this might be for you.
- **Compute, don't repeat.** Beyond merging, values can be *computed* at build time -
  `${VAR}` substitution (defaults, slicing, search/replace, case) and `$(( ))` integer
  arithmetic, opt-in per line via `$`/`%`. Assemble a `DATABASE_URL` from its parts,
  derive a port offset. There's **no shell**, so a password containing `$(rm -rf)` is
  data, never a command. (See [Expansion](#expansion) / [EXPANSION.md](https://github.com/anoyomoose/emergenv/blob/main/EXPANSION.md).)
- **It won't silently corrupt your secrets.** Every encryption is decrypted again in memory
  and verified against the original before anything is written.

If you're on a cloud platform with a real secret manager, use that. If you deploy to
dedicated servers from git, this is built for exactly that.

### Why not sops?

*emergenv* uses bare `age` rather than `sops`. That does cost some `sops` conveniences:
sops-capable IDE plugins, seeing from a git commit *which* variables changed (without their
contents), and structured merging of conflicting edits.

We accept those costs because `sops` is unreliable for `.env` files (specifically): numerous
bugs can leave the encrypted text not matching the original plaintext - worse than unusable,
it's downright dangerous. *emergenv*'s verify-on-write (above) is the direct response.

## License

Released under the MIT license

## Installation

You're going to need [age](https://github.com/FiloSottile/age). A lot of distros carry it,
`apt install age` if running something Debian-based.

Installation of *emergenv* itself is done through `pip`:

```pip install emergenv```

## Quick start

Reading docs can get complicated fast, so here's a small end-to-end example:

First:
- Run `emergenv init` to initialize
- Append your server's public key to `emergenv/authorized_keys`

Create the following files:

`dot.emerg.env`
```dotenv
@include database
$DATABASE_URL=postgres://${DB_USER}:${DB_PASS}@${DB_HOST}:${DB_PORT}/${DB_NAME}
```

The `$DATABASE_URL` line is *computed* - it is assembled from the other values at
build time (see [Expansion](#expansion)).

Alternatively (it is assumed you use the above form, not this one), you could import per-key:

```dotenv
@DB_HOST=database
@DB_PORT=database
@DB_NAME=database
@DB_USER=database
@DB_PASS=database
```

`dot.local.emerg.env` (do NOT commit, add to .gitignore)
```dotenv
DB_PASS=local-password
```

`emergenv/database.env`
```dotenv
DB_HOST=localhost
DB_PORT=5432
DB_NAME=default
DB_USER=default

# if we exclude this one, the @DB_PASS import-by-key would error for our local build, the @include would be fine
DB_PASS=invalid
```

`emergenv/staging/database.env`
```dotenv
DB_PASS=staging-password
```

`emergenv/production/database.env`
```dotenv
DB_PASS=production-password
```

Now encrypt everything:

```shell
emergenv encrypt --all
```

Check our status - should be all *AGE* (green):

```shell
emergenv status
```

Now three different versions of `.env` can be produced, locally or on the server (after 
a trip through git), each with a different `DB_PASS`:

```shell
emergenv build dot --profile production --no-local
emergenv build dot --profile staging --no-local
emergenv build dot
```

(`--no-local` isn't needed on the server as it doesn't have the local override file)

The contents of `.env` for the production profile:

```dotenv
# FROM: emergenv/database.age
DB_HOST=localhost
DB_PORT=5432
DB_NAME=default
DB_USER=default

# if we exclude this one, the @DB_PASS import-by-key would error for our local build, the @include would be fine
# DB_PASS=invalid

# FROM: emergenv/production/database.age
DB_PASS=production-password

# FROM: dot.emerg.env
# COMPUTED: $DATABASE_URL=postgres://${DB_USER}:${DB_PASS}@${DB_HOST}:${DB_PORT}/${DB_NAME}
DATABASE_URL=postgres://default:production-password@localhost:5432/default
```

Or if we also add the `--bare` parameter:

```dotenv
DB_HOST=localhost
DB_PORT=5432
DB_NAME=default
DB_USER=default
DB_PASS=production-password
DATABASE_URL=postgres://default:production-password@localhost:5432/default
```

Now that our files are encrypted, we can edit them like this:

```shell
emergenv edit production/database
```

## Deployment

This is the case *emergenv* is built for. The encrypted `.age` files are committed, so a
deploy is just: get the code onto the box (a `git pull`, or a push to a bare repo with a
`post-receive` hook that checks out), then run `emergenv build` to produce the `.env`.
No secret-injection step, no separate key inventory - the box already holds a key that
can decrypt.

The server needs *some* private key whose public half is an authorized recipient. Two
common choices:

- **A user key.** Any deploy user can use its own `~/.ssh/id_ed25519` (or `id_rsa`) -
  no root required. If the user doesn't have one yet, generate it:

  ```shell
  ssh-keygen -t ed25519
  ```

  then add `~/.ssh/id_ed25519.pub` as a recipient.
- **The SSH host key.** `/etc/ssh/ssh_host_ed25519_key` already exists on every server,
  which makes it a convenient default - paste its `.pub` into `authorized_keys` and
  there's no key to generate. The catch: it's readable only by root, so the build has
  to run as root (usually fine for a root-owned deploy hook). There's nothing special
  about the host key; a user key is equally valid and avoids running as root. Note that
  making the host key a recipient turns it secret-grade - it then decrypts every secret
  it can, so it matters wherever host keys get backed up or imaged
  ([Security](#security)).

After adding the server's public key to `emergenv/authorized_keys` (or a narrower set),
run `emergenv rekey` so the existing `.age` files gain the new recipient, and commit. On
the server the deploy step is then:

```shell
git pull
emergenv build dot --profile production --no-local
```

(`--no-local` is for clarity only - the server has no `.local` override anyway.)

> **If the build reports "no usable decryption key found"** and you are *not* root, the
> likely cause is that the only recipient is the root-owned host key. Either run the
> build as root, point `EMERGENV_KEY` at a key the deploy user can read, or add that
> user's own key as a recipient (then `rekey`).

## Directory structure

```text
git-root/
├── emergenv/
│   ├── .gitignore
│   ├── authorized_keys
│   ├── <target>.emerg.(age|env)
│   ├── <fragment>.(age|env)
│   ├── <profile>/
│   │   ├── authorized_keys
│   │   └── <fragment>.(age|env)
│   └── <target>/
│       ├── authorized_keys
│       ├── <fragment>.(age|env)
│       └── <profile>/
│           ├── authorized_keys
│           └── <fragment>.(age|env)
├── <target>.emerg.env
└── <target>.local.emerg.env
```

## What to commit

The golden rule: **plaintext secrets never enter git.** A committed file is either
encrypted (`.age`) or contains no secrets.

`emergenv/` ships its own `.gitignore` (created by `init`) that already keeps plaintext
fragments out of the store - so *inside* `emergenv/` you don't have to think about it.
The files *outside* `emergenv/` - the base file and the built output - are your
responsibility.

| Path | Commit? | Why |
| --- | --- | --- |
| `emergenv/**/*.age` | yes | encrypted; the whole point |
| `emergenv/**/authorized_keys` | yes | public keys only |
| `emergenv/**/*.env` | no | plaintext; the store's `.gitignore` handles this |
| `<target>.emerg.env` (base, in CWD) | only if it has no secrets | otherwise move it into `emergenv/` and encrypt it |
| `<target>.local.emerg.env` | no | local override; plaintext-only |
| `<target>.env` / `.env` (built output) | no | generated plaintext |

A base file that does nothing but `@include` other fragments holds no secrets, so it's
fine to commit in the working directory. The moment it contains a literal secret, move
it under `emergenv/` (as `emergenv/<target>.emerg.(age|env)`, see [Base file](#base-file))
so it gets encrypted like everything else.

Don't reach for a blanket `*.env` ignore. It would also catch `<target>.emerg.env`,
which you often *do* want to commit. Ignore the specific built outputs and the `.local`
file instead, e.g.:

```gitignore
.env                  # built output of the `dot` target
*.local.emerg.env     # local overrides
# ...and any other built <target>.env you produce
```

### Simplest possible use: just encrypt one file

If all you want is "encrypt my `.env` and regenerate it later", you need no profiles,
includes, or computed values at all (assuming you have already installed and used `init`):

```shell
mv whatever.env emergenv/whatever.emerg.env   # move it into the store
emergenv encrypt whatever.emerg               # -> emergenv/whatever.emerg.age (plaintext removed)
emergenv build whatever                       # -> whatever.env, exactly as before
```

`build whatever` finds `emergenv/whatever.emerg.age`, decrypts it in memory, and writes
`whatever.env`. Commit `emergenv/whatever.emerg.age`; the regenerated `whatever.env`
stays out of git.

## Encryption

Files are en/decrypted using `age`. The latest binary should be on the path. 

While *emergenv* supports plain text files as well, it will always prefer an `.age`
file over a `.env` file. Plain text files should not be committed, and by default are
ignored by the generated `.gitignore`.

One should endeavour to never have `.env` files in plain text unless you are actively
editing them - if another contributor updates the encrypted file, it's easy to overwrite
their updates with your own stale version. That is, unless you are not using encryption
*at all*.

### authorized_keys

The `authorized_keys` files are the same ones you use for SSH. Just paste the public
keys for everyone who needs to decrypt into it. Only `ssh-ed25519` and `ssh-rsa` are
supported (see the `age` binary).

Common sources for public keys:

```
Server (root only): 
/etc/ssh/ssh_host_ed25519_key.pub
/etc/ssh/ssh_host_rsa_key.pub

User (may also be server): 
~/.ssh/id_ed25519.pub
~/.ssh/id_rsa.pub
```

`emergenv/authorized_keys` is your base set, but you can override the set in every
subdirectory. Note that these do not extend each other, they are whole-file overrides.
When encrypting a file, the closest `authorized_keys` (walking up to `emergenv/`)
provides the recipients - so deeper directories can *narrow* who can decrypt.

Note: if your own key is not in the relevant `authorized_keys` file, encryption will
also fail.

## Decryption

*emergenv* will attempt decryption using these private keys:

```text
/etc/ssh/ssh_host_ed25519_key
/etc/ssh/ssh_host_rsa_key
~/.ssh/id_ed25519
~/.ssh/id_rsa
```

Every key whose `.pub` variant is listed in *any* `authorized_keys` is offered to
`age`, which uses whichever one actually decrypts the file.

This can be overridden using the `EMERGENV_KEY` environment variable, which
should point at a single private key file to use instead.

## Profiles

A *profile* is a named overlay - a subdirectory under `emergenv/` (and optionally under
a target directory) holding fragments that *extend* the base ones. They don't replace
the base fragment; its file and the profile's file are concatenated, and last-key-wins
applies to the result. So a profile that sets only `DB_PASS` changes just that key -
every other key from the base survives untouched. The obvious use is deployment
environments (`dev`, `staging`, `production`), but a profile is just a label: it works
equally well for regions (`eu`, `us`), tiers (`free`, `pro`), or any axis along which a
handful of values differ.

You select profiles at build time with `--profile`, and they layer left-to-right - when
the same key is set in more than one, the later one wins:

```shell
emergenv build dot --profile production
emergenv build dot --profile production,eu
```

`production,eu` concatenates the base fragment, then the `production` fragment, then the
`eu` fragment; last-key-wins then resolves any key set by more than one of them (see
[Resolving a fragment](#resolving-a-fragment) for the exact search order).

Profiles are entirely optional. If every environment shares the same values, or you keep
per-environment differences in the `.local` file, you may never define one - a build
with no `--profile` simply uses the base fragments.

## Syntax

`.emerg.env` files are ordinary `.env` files with a few extra constructs:

- [`@include <fragment>`](#include-fragment) - splice in the resolved contents of
  `<fragment>` (optionally filtered to a whitelist/blacklist of keys).
- [`@<key>=<fragment>`](#keyfragment) - emit a single line carrying the winning
  assignment of `<key>` (case-sensitive) from the resolved contents of `<fragment>`.
- [`@filter <key>...`](#filter-key) - restrict the **final output** to a
  whitelist/blacklist of keys (everything is still evaluated; hidden keys are just
  dropped from the output).
- [`$<key>=` / `%<key>=`](#expansion) - a *computed* assignment whose value is an
  expression (`${VAR}` substitution, `$(( ))` arithmetic) evaluated at build time.

`@include` and `@<key>=` both build on the same operation - **resolving a
`<fragment>`** into one merged env block - and differ only in how they consume it.
`@filter` (whole-file output filter) and the computed `$`/`%` forms are covered in
their own sections.

### Resolving a `<fragment>`

Base resolve order is:

```
emergenv/<fragment>.(age|env)
emergenv/<profile>/<fragment>.(age|env)
emergenv/<target>/<fragment>.(age|env)
emergenv/<target>/<profile>/<fragment>.(age|env)
```

For example, `database` referenced from `my.emerg.env` with profiles `all` and
`prod` is resolved by searching these paths, in order.

```
emergenv/database.(age|env)
emergenv/all/database.(age|env)
emergenv/prod/database.(age|env)
emergenv/my/database.(age|env)
emergenv/my/all/database.(age|env)
emergenv/my/prod/database.(age|env)
```

In short: each file contains overrides, the last one wins. The override is
per key, not per whole file.

Every file that exists is concatenated in that order. For each path the `.age`
file is preferred, and its `.env` sibling is then ignored. Resolution is
recursive: a resolved file may contain its own `@include` / `@<key>=` directives,
which expand the same way - but the search paths always remain relative to the
original working directory, never to the file doing the include. Finally,
last-wins resolution (see [Resolution order](#resolution-order)) is applied to
the concatenated result, yielding a single merged block.

Prefixing a `<fragment>` with `!` makes it an absolute path within `emergenv/`: no
search is performed, and only `emergenv/<fragment>.(age|env)` is resolved (with the
`!` stripped). For example, `@include !database` resolves exactly
`emergenv/database.(age|env)`, ignoring every profile and target variant. The
`.age`-over-`.env` preference, recursion, and the no-match error still apply.

If `<fragment>` matches no file at all, *emergenv* aborts with an error.

Note that if the current working directory does not contain an `emergenv/` directory,
ancestors are searched, stopping at a git root boundary.

### `@include <fragment>`

The directive line is replaced by the entire merged block for `<fragment>`.

It may be followed by a space-separated key list to splice only part of the
fragment:

- `@include <fragment> K1 K2` - keep only the lines that assign `K1`/`K2` (a
  **whitelist**; the fragment's other keys, comments and blanks are dropped).
- `@include <fragment> !K1 !K2` - keep everything *except* the lines that assign
  `K1`/`K2` (a **blacklist**, so comments and other keys are preserved).

You cannot mix included and excluded keys in one directive, and a key that matches
no assignment in the fragment is an error.

Unlike `@<key>=<fragment>`, the whitelist keeps **every** line for a key (all its
overrides, with `# FROM:` provenance). Evaluation still happens in the assembled
output, not in the fragment - but because all of a key's lines travel together, a
self-referential chain survives: `@include f MY_VAR` over `MY_VAR=1` /
`$MY_VAR=$(( MY_VAR + 1 ))` yields `MY_VAR=2`, because the `MY_VAR=1` line is spliced
in just above the expression. (`@MY_VAR=f` instead carries only the final expression,
evaluated where the directive sits against the surrounding build.) So if a kept
computed line references a key the filter dropped, that key must be defined elsewhere
in the build or expansion errors.

### `@<key>=<fragment>`

The directive line is replaced by a single line carrying the **winning assignment**
of `<key>` within the merged block for `<fragment>` (the last one, per last-wins). If
`<fragment>` resolves but contains no `<key>`, *emergenv* aborts with an error.

What travels is the winning *line*, not a pre-resolved value. If that line is a
computed assignment (`$<key>=` / `%<key>=`, see [Expansion](#expansion)), its marker
and template are copied verbatim and evaluated **where the directive sits** - against
the surrounding build, not inside `<fragment>` - and the template's dependencies are
**not** carried along. So a pulled `$URL=${HOST}/db` reads `HOST` from the
destination (and errors if `HOST` is undefined there); use `@include` instead when
you want a formula computed in the fragment's own context. See
[EXPANSION.md](https://github.com/anoyomoose/emergenv/blob/main/EXPANSION.md) for the
exact rules.

### `@filter <key>...`

`@filter` selects which keys survive to the **final output**, applied *after* all
evaluation - so you can compute many interdependent variables but emit only a few.

- `@filter K1 K2` - keep only these keys (whitelist).
- `@filter !K1 !K2` - keep everything except these (blacklist).

It uses the same key grammar as `@include`: mixing included and excluded keys in one
`@filter` is an error, and `@filter` with no keys is an error. A whitelist key that
isn't in the output is an error; an absent blacklist key is harmless.

Filtered-out keys are **still evaluated** - a kept value computed from a hidden key is
correct - they are just commented out of normal output (and therefore dropped entirely
by `--bare`). `@filter` is whole-file: it may appear only in the base or `.local` file
(never in an included fragment), it may appear more than once (applied in order), and
its position among other lines doesn't matter. `emergenv build --no-filter` ignores it.

Note `@filter=x` (no space) is still a *keyref* for a key literally named `filter`;
the directive needs a space: `@filter x`.

### Constraints

A `<fragment>` may contain `/` (but must not start with one) and must never contain
`..`. It may begin with `!` to force an absolute path within `emergenv/` (see
above), in which case the remainder follows the same rules. Take care that
directives do not form a cycle.

## Expansion

Besides the `@include` / `@<key>=` directives, a value can be *computed* from other
values with a marked assignment:

- `$<key>=<template>` - expand `<template>` using the other built keys.
- `%<key>=<template>` - same, but the host environment is also available as a
  fallback (a locally-defined key always wins over the environment).

Templates support `${VAR}` substitution (with defaults, substring, search/replace,
case, ...) and `$(( ))` integer arithmetic. Evaluation happens last, in order, so a
template may only use values defined *above* it. Plain `<key>=<value>` lines are
never expanded - expansion is opt-in per line via `$`/`%`. Both markers work with
`export` and travel through `@include` / `@<key>=`.

```dotenv
DB_HOST=localhost
DB_PORT=5432
$DATABASE_URL=postgres://${DB_USER:-app}@${DB_HOST}:${DB_PORT}/${DB_NAME}
```

See [EXPANSION.md](https://github.com/anoyomoose/emergenv/blob/main/EXPANSION.md) for the complete, exact specification of every
supported form (and how it deliberately differs from bash).

## Base file

`<target>.emerg.env` files can be located in the current working directory. If
not found, it will also look for `<target>.emerg.(age|env)` in the `emergenv/`
directory, allowing it to partake in all encryption operations: there it is just
a fragment named `<target>.emerg`, so `emergenv encrypt prod.emerg` encrypts
`emergenv/prod.emerg.env` to `emergenv/prod.emerg.age` (and `decrypt`, `status`,
and `clean` work the same way). The `.emerg` infix keeps these base files in a
separate namespace from ordinary fragments, and ensures encryption never
overwrites the build input.

A base file in the current working directory shadows the one in `emergenv/` (it
doesn't merge), so use one or the other, not both. Either location honours `/` in
`<target>` to reach into subdirectories - relative to the working directory for
the CWD base, relative to `emergenv/` for the fallback.

Rule of thumb: if the base file doesn't actually contain any secrets, keep
it in the normal directory, next to where the built output file would
occur. If you don't do extensive inclusion and have secrets directly
inside of it, place it in `emergenv/`.

### Local extension

The `<target>.local.emerg.env` file (always relative to the working directory)
is suffixed to the base file before processing, and may contain its own
`@include` / `@<key>=` directives. It is plaintext-only and should not be
committed.

## Resolution order

In the final output file, the last variable of the same name wins. This is enforced
by automatically commenting out preceding instances.

## Command reference

Note: `emergenv/` is located by searching the current working directory and then
its ancestors, stopping at a `.git` boundary (the repo root) - so a sibling
project's store is never picked up by accident. All `<fragment>`'s are relative to
that `emergenv/`; all `<target>`'s (and the build output) remain relative to the
current working directory itself.

Unlike the `@include` / `@<key>=` directives, a `<fragment>` given to these commands
is an **exact** path under `emergenv/` (no profile/target searching). For
shell-completion convenience a leading `emergenv/` and a trailing `.age`/`.env`
are stripped, so `database`, `emergenv/database`, and `emergenv/database.age` all
refer to the same name. A `<fragment>` may still contain `/` to reach files inside
profile and target subdirectories.

`.` is never a valid `<fragment>` (use `dot` for the `.env` target), and `emergenv`
is reserved as a first path component since it is ambiguous with the stripped
prefix.

### init

Creates `emergenv/`, `emergenv/.gitignore`, and a base
`emergenv/authorized_keys` (containing the public keys from `id_ed25519.pub` and
`id_rsa.pub` in `~/.ssh` if either is present, otherwise empty)

### edit [`<fragment>`|--all] [--wait]

Decrypts `<fragment>.age` to `<fragment>.env`, opens it with `$VISUAL`, `$EDITOR`, 
`/usr/bin/editor`, or `vi`, and re-encrypts it after the editor closes.

If `<fragment>.age` doesn't exist, or `<fragment>.env` already exists, *emergenv* aborts with
an error.

If no editor can be executed or `--wait` is passed, instead it waits for 
the user to press ENTER between decrypting and re-encrypting, allowing the user to edit the
file(s) in for example their IDE.

If `--all` is passed instead of a `<fragment>`, *all* files are decrypted, it waits for the
user to press ENTER, then *all* files are re-encrypted. `--all` will refuse to work unless
`status` returns an all-green result (only encrypted files exist). One should not modify
the `.age` files or use any `git` commands during this procedure. (Essentially this is
short-hand for `status || exit; decrypt --all; wait; encrypt --all;`).
On failure your edits survive as `.env` files; fix the cause and use `encrypt --all` and/or
`clean`.

The decrypted file(s) are removed after having been re-encrypted, for both `<fragment>` and
`--all` variants.

### decrypt [`<fragment>`|--all]

Decrypts `<fragment>.age` to `<fragment>.env`, aborting with an error if either `<fragment>.age`
doesn't exist, or `<fragment>.env` already exists.

If `--all` is passed rather than a `<fragment>`, all `.age` files are decrypted,
**overwriting** any existing `.env` files.

### encrypt [`<fragment>`|--all] [--keep]

Encrypts `<fragment>.env` to `<fragment>.age`, **overwriting** `<fragment>.age` if it exists, and
aborting with an error if `<fragment>.env` doesn't exist.

If `--all` is passed rather than a `<fragment>`, all `.env` files are encrypted
(**overwriting**).

For both cases, unless `--keep` is passed, the `.env` files are deleted after
encryption.

Before overwriting, an existing `<fragment>.age` is decrypted and compared: if it
already decrypts to the same plaintext, it is left untouched. (`age` ciphertext
is non-deterministic, so re-encrypting unchanged contents would otherwise churn
git history and the file's timestamp for no real change.)

### rekey

Re-encrypts every fragment to its current recipients. Run this after editing any
`authorized_keys` file, so the new recipient set is applied across the store.

It is similar to `encrypt --all`, but **always** rewrites each `<fragment>.age` -
the plaintext is unchanged, but the recipient set is not, and `age` ciphertext
does not expose its recipients for the usual unchanged-skip to compare against.

It works entirely in memory, in two passes, so that a *detectable* problem aborts
before anything is written:

1. **Verify.** Every `<fragment>.age` is decrypted in memory (you must be a
   recipient of all of them, as with any decryption). If a plaintext
   `<fragment>.env` sits beside an `.age` and their contents **differ**, *emergenv*
   cannot know which is authoritative - perhaps the `.age` is newer from a pull,
   perhaps the `.env` is a pending edit - so it aborts and lists the offenders.
   Make sure your working tree is clean first (`encrypt`, `decrypt`, or `clean`).
   A fragment that is `.env`-only (no `.age`) is adopted: its `.age` will be
   created.
2. **Write.** Each collected plaintext is re-encrypted to its nearest
   `authorized_keys`, overwriting the `<fragment>.age`.

`.env` files are never created, written, or removed. A matching `.env` you had is
left in place - run `clean` afterwards if you want the plaintext gone (including
for any `.env`-only fragment that was just adopted).

Note that pass 2 is not atomic: if it fails partway (for example a verify-on-write
error), some `.age` files will already be on the new recipient set and others not.
Fix the cause and run `rekey` again - it is safe to repeat.

Because of this, run `rekey` on a **clean working tree** (everything committed) and
commit its result as a single change. Then a rare partway failure is trivially
recoverable - `git checkout` back to the clean state and retry, rather than reasoning
about a half-rewritten tree. The pass-1 pre-flight makes such a failure unlikely; the
clean-tree habit makes it cheap regardless.

There is intentionally **no per-file rekey**. It operates on the whole store on
purpose: picking individual files makes it far too easy to leave one behind, and
an omitted file is invisible - `age` ciphertext does not reveal its recipients, so
nothing (not even `status`) can later tell you a fragment is still encrypted to a
key you meant to revoke. Re-keying everything is the safe default; the only cost
is git churn, which is cheap and, as a single commit, doubles as a clean record
that a rotation happened.

### status

As a pre-flight, `status` first verifies you are a recipient of **every**
`authorized_keys` set under `emergenv/` - it probes each set with your identities
(honouring `EMERGENV_KEY`, exactly as real decryption would). If any set does not
include you, it prints those sets and aborts with exit code `3`, since you would
otherwise only discover the lockout when an `encrypt`/`edit`/`rekey` write fails
for a file governed by that set. When you are a recipient everywhere it says
nothing and proceeds to the listing.

It then lists every `<fragment>` and its state, one per line as `<fragment> <status>`:

| status | meaning | colour | code |
| --- | --- | --- | --- |
| `AGE` | only the encrypted file exists | green | 0 |
| `AGE+ENV MATCH` | both exist and the `.env` matches the `.age` | orange | 1 |
| `ENV` | only the plaintext file exists | orange | 1 |
| `AGE+ENV MISMATCH` | both exist but differ | red | 2 |
| `ERROR` | the `.age` could not be decrypted | red | 3 |

Every fragment is listed even if some fail to decrypt. The process exit code is the
highest code shown (so it is `0` only when everything is encrypted-only, great for
a pre-commit hook). Colours are emitted only to a terminal, and suppressed when
`NO_COLOR` is set.

### clean

Deletes all `.env` files for which a `.age` file exists with the same contents.

### build <target> [--profile <profile>[,<profile>[...]]] [--no-source] [--bare] [--no-local] [--no-filter] [--output <filename>] [--verbose] [--trace <vars>]

Builds `<target>` and writes the result to `<target>.env` in the working
directory (which should not be committed). The base file (and its optional
`.local` extension) is resolved as described under [Base file](#base-file).

A `<target>` may be given with a `.emerg.(age|env)`, `.local.emerg.(age|env)`, or
`.env` suffix (stripped for shell-completion convenience).

`--profile` takes a comma separated list of profiles, processed in the passed order.
A profile must be a single path segment (no `/`).

By default each emitted line is annotated with a `# FROM: <path>` comment marking
the source file it came from (one header per contiguous run, so it isn't repeated
per line). Overridden values stay visible - commented out - under their own
source, so you can see exactly where every value came from and what shadowed it.
A computed (`$`/`%`) line is similarly annotated with a `# COMPUTED: <directive>`
comment showing the original expression above its resolved value. Pass
`--no-source` to omit both kinds of provenance comment (`# FROM:` and
`# COMPUTED:`), leaving only the resolved assignments.

Pass `--bare` to strip the output of all comments and blank lines entirely,
leaving only the winning assignments (one line per variable).

For tidy output, blank-line handling is normalised (whitespace-only lines count
as blank): any run of consecutive blank lines collapses to a single blank, files
are separated by one blank line, leading/trailing blanks are dropped (the output
never ends with a blank line), and a `# FROM:` header is shown only before actual
content - a source that contributes nothing but a blank line gets no header.

Pass `--no-local` to ignore the `<target>.local.emerg.env` override and build from
the base file alone (useful for reproducing the committed/deploy build while a
local override is present).

Pass `--no-filter` to ignore any [`@filter`](#filter-key) directives and emit every
key (everything is still evaluated, so this just un-hides the filtered keys).

Pass `--output <filename>` to override the output filename, use `-` as filename
to write to stdout.

While building, progress is logged to stdout - the target name, profiles, the
resolved `emergenv/`, base, and `.local` paths (absolute), each imported fragment
(`emergenv/`-relative, only files that actually resolved), and the output path:

```text
building: dot
profiles: dev staging
emergenv: /home/user/myproject/emergenv
target: /home/user/myproject/emergenv/dot.emerg.age
local: /home/user/myproject/dot.local.emerg.env
importing: database.age
importing: dot/dev/database.age
writing: /home/user/myproject/.env
```

When `--output -` is used these logs are dropped entirely so stdout carries only
the built environment (errors still go to stderr).

Pass `--verbose` (`-v`) to replace the terse `importing: <file>` lines with a full
breakdown of every search path tried - both the `.age` and `.env` variant of each -
colour-coded: green (using the encrypted file, or nothing there), orange (building from
or leaving plaintext), red (a plaintext fragment that was never encrypted, plaintext
that has drifted from its `.age`, or a cwd base shadowing one in `emergenv/`). The base
file and `.local` are broken down the same way. It is informational only and never
changes the exit code; like the other logs it is suppressed under `--output -`.

```text
target: dot
  - missing: dot.emerg.env [cwd]
  - using: emergenv/dot.emerg.age
local: missing /home/user/myproject/dot.local.emerg.env
importing: database
  - using: database.age
  - missing: database.env
  - missing: dev/database.(age|env)
  - using: dot/dev/database.age
  - ignoring: dot/dev/database.env [age-preferred,match]
writing: /home/user/myproject/.env
```

Pass `--trace <vars>` to print, after the build, the full assignment history of
selected variables - every place the value was set or changed, nested by `@include`
/ `@<key>=` and source file, with the final value in the header. Like `--profile` it
is comma-separated and repeatable (`--trace A,B --trace C`); `--trace '*'` (quote it,
or the shell will expand the `*`) or the convenience flag `--trace-all` traces every
variable in the output. A name that isn't in the built output is an error. Like the
other logs it goes to stdout and is suppressed under `--output -`.

A computed (`$`/`%`) line shows both the template and the value it resolved to;
keyref candidates that lost the inner selection are marked `[ignored]` (they are
scanned but never evaluated). Example (`--trace MY_VAR`):

```text
MY_VAR=1
  - @include base
    - emergenv/base.age
      MY_VAR=1
    - emergenv/profile/base.age
      MY_VAR=2
  - $MY_VAR=$(( MY_VAR + 1 ))
    MY_VAR=3
  - MY_VAR=1
```

(`@MY_VAR=base` would show the same per-source breakdown but mark the candidates it
didn't pick `[ignored]`; a keyref that pulls a *computed* line re-evaluates it where
the keyref sits, per [EXPANSION.md](https://github.com/anoyomoose/emergenv/blob/main/EXPANSION.md).)

#### The `.env` output (`dot` target)

To produce a plain `.env` file, use the reserved target name `dot`: it behaves
like any other target - base `dot.emerg.env` (or `emergenv/dot.emerg.(age|env)`),
local `dot.local.emerg.env`, override directory `emergenv/dot/` - except the
final output is written as `.env` rather than `dot.env`. Consequently `dot` is
reserved: you cannot define a target whose output is literally `dot.env`. The
bare names `.` and `""` are never valid as a `<target>` or `<fragment>`.

## Recipes

### docker-compose

Two distinct uses, both common:

**Variables for the compose file itself.** Compose automatically reads a `.env` from the
project directory and uses it to interpolate `${VAR}` references in `docker-compose.yml`
(image tags, ports, volume paths, ...). Build the `dot` target and Compose picks it up
with no extra configuration:

```shell
emergenv build dot   # -> .env
```

**Environment for a container.** To set the variables a service actually runs with, build
a separate file and point that service's `env_file` at it:

```shell
emergenv build app   # -> app.env
```

```yaml
services:
  app:
    env_file: app.env
```

Keeping the two files separate (the `.env` Compose interpolates with, and the `app.env`
a container runs with) keeps build-time and runtime variables from bleeding into each
other.

### Consume it from systemd

```ini
[Service]
EnvironmentFile=/srv/app/.env
ExecStart=/srv/app/run
```

Regenerate the file (`emergenv build dot`) before `systemctl restart`.

### Rotate a secret

```shell
emergenv edit production/database   # decrypts, opens $EDITOR, re-encrypts on close
git commit -am "rotate db password"
```

The `.age` diff shows *that* it changed without revealing the new value. Redeploy as
usual.

### Revoke someone's access

Remove their public key from **every** `authorized_keys` file that lists it.
`authorized_keys` files are whole-file overrides, not additive - a subdirectory's set
doesn't inherit from `emergenv/authorized_keys`, so a key left in even one subdir set
keeps decrypting the fragments that set governs. Then re-encrypt every fragment to the
new recipients:

```shell
emergenv rekey
git commit -am "revoke <name>"
```

`rekey` rewrites *all* `.age` files (see [rekey](#rekey)); there is deliberately no
per-file version, because an `.age` doesn't reveal its recipients and a forgotten file
would silently stay readable by the revoked key.

Re-keying only stops the removed key decrypting *future* commits - it can still decrypt
the old `.age` blobs already in git history. So when access genuinely needs to end - a
compromised key, **or a person who has left** - also **rotate the affected secret
values** (`edit` + commit), not just the recipient set. See [Security](#security).

### Onboard a new server

Add the server's public key - its host key, or a deploy user's `~/.ssh/id_ed25519.pub`
(see [Deployment](#deployment)) - to every `authorized_keys` file governing a fragment
the server must read. Since `authorized_keys` files are whole-file overrides (a
subdirectory's set replaces the base one rather than extending it), adding only to
`emergenv/authorized_keys` leaves any fragment in a subdirectory that has its own set
undecryptable by the new key. Then:

```shell
emergenv rekey
git commit -am "add <server> as recipient"
```

The server can decrypt on its next pull.

## Security

**What's protected.** Secret *contents* never enter git in the clear. Each `.age` file
is encrypted to a specific set of recipients, so a leaked repo - or a contributor who
isn't a recipient - reveals nothing but the fact that an encrypted blob exists and
changed. `age` also hides the recipient list: the ciphertext doesn't say who can decrypt
it (which is exactly why [rekey](#rekey) is all-or-nothing).

**Integrity.** Every encryption is decrypted again in memory and compared against the
original before anything is written, so a corrupt or mismatched write is caught rather
than silently committed. This is the core reason *emergenv* uses bare `age` instead of
`sops` (see [Why not sops?](#why-not-sops)).

**No shell.** Expansion is pure-Python with no command execution - there is no command
substitution at all. A value like `pass=$(rm -rf ~)` or `` pass=`whoami` `` is inert
data, never a command, even on a `$`/`%` computed line.

**What's *not* protected - and is your responsibility:**

- **The built output is plaintext.** `.env` / `<target>.env` on disk holds real secrets.
  Keep it out of git (it already should be) and restrict its file permissions; on a
  server, only the deploying user and the service that reads it need access.
- **Recipients see everything they're a recipient of.** Access is per-directory
  (`authorized_keys` granularity), not per-value - there's no way to hand someone a
  single key out of a fragment they can otherwise decrypt.
- **An SSH host key used as a recipient becomes secret-grade.** Using the server's
  `/etc/ssh/ssh_host_*_key` as the decryption identity is convenient (see
  [Deployment](#deployment)), but it means that key now decrypts every secret it is a
  recipient of - and host keys get backed up, snapshotted, and copied with server images
  in ways their owners rarely treat as sensitive. Anywhere that key lands, your secrets
  can be decrypted. A dedicated deploy-user key (generate one just for this) keeps the
  blast radius to a single key you manage deliberately, and avoids running the build as
  root.
- **Revocation is forward-only.** Removing a key and re-keying stops it decrypting
  *future* commits, but git history still contains the old `.age` files, and the revoked
  key can still decrypt those. So whenever access genuinely needs to end - a compromised
  key, **or a person who has left** - **rotate the secrets themselves** (`edit` + commit),
  not just the recipient set. Re-keying changes who can read new commits; only a new
  secret *value* invalidates what the old key already saw.

## pyproject.toml integration

If you build several targets on every deploy, you can declare them once in
`pyproject.toml` and run the whole chain with a single command:

```shell
emergenv pyproject --profile production
```

instead of maintaining a script of `emergenv build …` lines. The committed
`pyproject.toml` owns the **static structure** - which target builds to which
output, and the output *format*. The command line owns the **per-deploy axis**
(`--profile`) and build selection (`--group`).

> Requires **Python 3.11+** (it uses the standard-library `tomllib`). On older Python, run `emergenv build` per target.

### Defining builds

```toml
[tool.emergenv]
# Defaults inherited by every build below; each build can override them.
bare = true
local = false                 # = --no-local on every build

[[tool.emergenv.builds]]
target = "env1"               # -> env1.env

[[tool.emergenv.builds]]
target = "env2"
output = "envs/bla.env"       # override the default output path

[[tool.emergenv.builds]]
target = "metrics"
group = "observability"
profile_post = ["eu"]         # always layer 'eu' on top of the CLI profile

[[tool.emergenv.builds]]
target = "fixture"
profile = ["test"]            # forced profile; ignores --profile
```

`[[tool.emergenv.builds]]` is an array, so builds run **in file order**, and the
same target may appear more than once (e.g. one `dot` per environment).

`emergenv pyproject --profile production` then runs, in order:

```text
emergenv build env1    --profile production --bare --no-local
emergenv build env2    --profile production --bare --no-local --output envs/bla.env
emergenv build metrics --profile production,eu --bare --no-local
emergenv build fixture --profile test --bare --no-local
```

It is a thin wrapper: it resolves the config into `emergenv build` commands and
shells out to each (logging every command), rather than reimplementing the build.

### Keys

| Key | Type | Where | Effect |
| --- | --- | --- | --- |
| `target` | string | build only | the build target (required) |
| `output` | string | build only | output path (default `.env` / `<target>.env`) |
| `group` | string \| list | build only | tag for `--group` selection |
| `bare` | bool | global + build | `--bare` when `true` |
| `local` | bool | global + build | `--no-local` when **`false`** |
| `source` | bool | global + build | `--no-source` when **`false`** |
| `filter` | bool | global + build | `--no-filter` when **`false`** |
| `verbose` | bool | global + build | `--verbose` when `true` |
| `trace` | string \| list | global + build | `--trace x,y`, or `--trace-all` if `"*"` |
| `profile` | string \| list | global + build | **forces** the profile list |
| `profile_pre` | string \| list | global + build | profiles prepended to `--profile` |
| `profile_post` | string \| list | global + build | profiles appended to `--profile` |

The boolean flags are **inverted** from the CLI on purpose: a config file reads
better as positive state, so `local = false` produces `--no-local`. Any of
`group`/`profile`/`profile_pre`/`profile_post`/`trace` may be written as a single
string or a list (`"a,b"`, `["a","b"]`, and CLI `--profile a,b` are equivalent).

### Profiles: the one rule worth knowing

The profile for each build resolves as **`profile_pre` + `--profile` (CLI) +
`profile_post`** - *unless* a plain `profile` is set, which **forces** that list
and ignores `--profile` entirely. So:

- Use `profile` for a build that must never vary (a test fixture).
- Use `profile_pre` / `profile_post` (and leave `profile` unset) when you want the
  deploy's `--profile` to participate, wrapped by fixed overlays.

### Overriding format from the command line

The format/diagnostic booleans can be overridden for a whole run with paired
flags - `--bare`/`--no-bare`, `--local`/`--no-local`, `--source`/`--no-source`,
`--filter`/`--no-filter`, `--verbose`/`--no-verbose`. Each is **tri-state**:

- omitted ⇒ use whatever the TOML resolves to (the normal case),
- given ⇒ force that value for **every** build, beating the TOML.

So even though the committed config sets `bare = true`, a local run can ask for
the commented, annotated output:

```shell
emergenv pyproject --profile staging --no-bare --local
```

`--output` is deliberately not offered (it can't apply across multiple builds);
output stays per-build in the TOML. `--trace`/`--trace-all`, if given, replaces
the TOML `trace` for every build.

### Selecting a subset

`--group` runs only the builds tagged with a matching `group` (repeatable and
comma-separated, like `--profile`); with no `--group`, every build runs.

```shell
emergenv pyproject --profile production --group observability   # only 'metrics'
```

### Preview

`--dry-run` prints the exact command list without running anything - ideal for
confirming what a deploy will do before it touches your env files:

```shell
emergenv pyproject --profile production --dry-run
```

Builds run sequentially and stop on the first failure; like `rekey`, this is not
atomic - a failure partway leaves the already-built outputs in place.
