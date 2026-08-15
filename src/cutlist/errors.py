"""Error types.

One rule governs this module: **failures are loud**.

Defaulting the other way costs more than every other class of bug combined. A
probe failure swallowed into a duration of 0.0 becomes a one-frame clip, a
structurally valid project and a cheerful success message. A missing graphics
asset degrades to a blank track with no warning. An encoder exits 0 on a project
that renders entirely black.

Every one of those is a *silent wrong output*: the operator believes the tool
worked. Of the fifty-three failure modes recorded while building this kind of
pipeline, thirty-one were of exactly this kind. So nothing here returns a
plausible default on failure -- it raises, and it says what to do about it.
"""

from __future__ import annotations


class CutlistError(Exception):
    """Base for every error this package raises deliberately.

    Carries an optional `hint`: the literal next action a user can take. A
    failure message that does not say what to do next is a support ticket.
    """

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        if self.hint:
            return f"{self.message}\n  -> {self.hint}"
        return self.message


class ToolNotFound(CutlistError):
    """A required external binary could not be located."""


class ToolTooOld(CutlistError):
    """The located binary exists but predates a feature the config needs."""


class CapabilityMissing(CutlistError):
    """The located binary lacks a filter, encoder or format the config needs.

    Raised *before* rendering, never during. A build that was going to fail
    forty minutes in should fail in the first second instead.
    """


class ProbeError(CutlistError):
    """A media file could not be probed, or reported something unusable."""


class ConfigError(CutlistError):
    """The project config is malformed, or references something absent."""


class RenderError(CutlistError):
    """ffmpeg was invoked and did not succeed."""


class VerificationError(CutlistError):
    """The output was produced but does not match what was asked for.

    This is the class that catches the failures a zero exit code hides: a black
    render, a silent audio track, a frame count that drifted, a delivered
    bitrate that is not the requested one.
    """
