# Licensing

`cutlist` is MIT (see `LICENSE`). This file exists so the one genuinely
non-obvious question — *what happens with ffmpeg?* — is answered once and does
not get re-litigated in a future pull request.

## The plugin's own code

MIT. Nothing here derives from GPL source. The engine writes ffmpeg filter
graphs — a documented command-line interface — and spawns a binary. Emitting a
command line is not copying code.

There is no patent grant and none is needed. The plugin ships no encoder and no
decoder, so the H.264/AVC patent pool — the only patent question anywhere near
this subject — concerns the ffmpeg the *user* installed, not this repository.

## ffmpeg: invocation is not linking

ffmpeg is LGPL-2.1+ by default, and **GPL as a whole** when built
`--enable-gpl` — which most distributions do, because that is what enables
libx264 and a number of filters this tool can emit.

Running it as a separate process over its documented CLI is arm's-length
aggregation. A permissively licensed program that spawns `ffmpeg` does not
become a derivative work of ffmpeg, and no GPL obligation attaches to this
source on that basis.

**The line is distribution.** The moment a release asset, a wheel, a Docker
image, a CI cache artifact or a "portable bundle" contains an `ffmpeg`,
`ffprobe` or `libav*` binary, the distribution *as a whole* falls under the GPL
and owes a corresponding-source offer for those binaries.

So:

- **Never vendor the toolchain.** Detect it, or tell the user how to install it.
  CI fails the build if any tracked file matches `*.exe`, `*.dll`, `*.so`,
  `*.dylib`, or is named like a toolchain binary.
- **Never link libav\* or use ffmpeg Python bindings that do.** The subprocess
  boundary is a licensing feature, not just an implementation detail. If you are
  tempted to "optimise" `tools.py` into an in-process binding, this paragraph is
  why you should not.

The same reasoning and the same rule apply to MLT/`melt`, should anyone ever add
an optional backend for it.

## Fonts

**This repository ships no font binaries**, and that is deliberate rather than
incidental. Default typography is system font stacks.

If you add a font file, the OFL and most other font licences require the licence
text and copyright notice to travel with every copy. CI enforces this: any file
under a `fonts/` directory fails the build unless a licence file sits beside it.
`scripts/fetch_fonts.py` downloads permissively licensed families on demand and
writes their licence alongside — use that instead of committing a `.ttf`.

Note also that font licences that reserve a font name (OFL §3) forbid reusing
that name on a modified version, so subsetting or re-hinting requires renaming
the family.

## Output

Rendered video carries no licence from the encoder that produced it. libx264's
GPL applies to the encoder binary, not to the frames it emits.

If ProRes output is ever added, do not stamp the Apple vendor atom
(`-vendor apl0`) by default: it labels a file produced by ffmpeg's independent
reimplementation as Apple-originated. Expose it as an explicit opt-in for tools
that reject non-Apple vendor atoms, with a comment saying so.

## Sample media

Any media in this repository must be one we can license. Test fixtures are
generated synthetically by ffmpeg's `lavfi` sources at test time — which keeps
the repository small and sidesteps model releases, identifiable people and
places, visible licence plates and location metadata entirely.
