"""Deterministic, bounded written-bar -> performed-bar order expansion.

Pure function over a normalized per-written-bar fact list; no file I/O, no
dependency on `music_assets.py`, `guitarpro` or Node. Both the modern GPIF
path and the legacy GP3/4/5 path in `music_assets.py` build the same input
shape and call `expand_performed_order` here so there is exactly one
expansion engine, not one per format.

The repeat/alternate-ending/navigation-direction state machine below is a
direct, line-by-line port of the installed alphaTab 1.8.4 MIDI playback
controller (`skills/vp-music/tools/node_modules/@coderline/alphatab/dist/
alphaTab.core.mjs`, class `MidiPlaybackController`: `processCurrent`,
`moveNext`, `_moveNextWithDirections`, `_moveNextWithNormalRepeats`,
`_handleDaCapo`, `_handleDalSegno`, `_handleDaCoda`, `_findJumpTarget*`).
Reusing that verified reference algorithm (rather than a new, unverified
interpretation) is deliberate: it is the same logic alphaTab itself uses to
play GP3/4/5/GPX/GPIF files back, so normalized facts extracted from any of
those formats expand identically here.

Supported, bounded:
  * linear order (no repeats/directions)
  * repeat open/close with a normalized TOTAL pass count
  * nested and adjacent repeats (implicit open = song start or right after
    the previous group's close at the same nesting level)
  * alternate endings as 1-indexed pass masks, including multi-bar
    propagation (a bar with no explicit mask inherits the previous explicit
    mask within the same bracket, exactly like alphaTab's
    `_previousAlternateEndings`)
  * anacrusis/pickup bars (passed through; no special engine handling since
    they behave like any other written bar in the sequence)
  * the 19 documented GP5/GPIF navigation directions (Coda/Double Coda/
    Segno/Segno Segno/Fine targets and their D.C./D.S./D.S.S./"to coda"
    jumps), with repeats reset (ignored) after a jump, matching the
    reference behaviour

Explicitly NOT guessed; reported as diagnostics instead:
  * a `repeat_open` with no matching `repeat_close` anywhere in the song
    (malformed/unclosed repeat) -> `status="unsupported"`, no output
  * expansion exceeding the bounded step/output limits (possible runaway
    structure) -> `status="unsupported"`, no output

A navigation jump whose target marker (Segno/Segno Segno/Coda/Double Coda)
is not present anywhere in the song is handled the same way the reference
implementation handles it: the jump is silently ignored and playback
continues normally. That is not a guess (it is the documented fallback of
the reference engine this module ports), but it is still reported as a
non-fatal diagnostic for transparency.
"""

MAX_STEPS = 20_000
MAX_OUTPUT_BARS = 20_000
MAX_ALTERNATE_ENDING = 8  # matches alphaTab's MasterBar.MaxAlternateEndings

TARGET_DIRECTIONS = frozenset({
    "TargetFine", "TargetSegno", "TargetSegnoSegno", "TargetCoda", "TargetDoubleCoda",
})
JUMP_DIRECTIONS = frozenset({
    "JumpDaCapo", "JumpDaCapoAlCoda", "JumpDaCapoAlDoubleCoda", "JumpDaCapoAlFine",
    "JumpDalSegno", "JumpDalSegnoAlCoda", "JumpDalSegnoAlDoubleCoda", "JumpDalSegnoAlFine",
    "JumpDalSegnoSegno", "JumpDalSegnoSegnoAlCoda", "JumpDalSegnoSegnoAlDoubleCoda",
    "JumpDalSegnoSegnoAlFine", "JumpDaCoda", "JumpDaDoubleCoda",
})
ALL_DIRECTIONS = TARGET_DIRECTIONS | JUMP_DIRECTIONS

# (jump direction, new state) in the reference's checked order.
_DA_CAPO_JUMPS = (("JumpDaCapo", 1), ("JumpDaCapoAlCoda", 2),
                  ("JumpDaCapoAlDoubleCoda", 3), ("JumpDaCapoAlFine", 4))
# (jump direction, new state, target marker) in the reference's checked order.
_DAL_SEGNO_JUMPS = (
    ("JumpDalSegno", 1, "TargetSegno"), ("JumpDalSegnoAlCoda", 2, "TargetSegno"),
    ("JumpDalSegnoAlDoubleCoda", 3, "TargetSegno"), ("JumpDalSegnoAlFine", 4, "TargetSegno"),
    ("JumpDalSegnoSegno", 1, "TargetSegnoSegno"), ("JumpDalSegnoSegnoAlCoda", 2, "TargetSegnoSegno"),
    ("JumpDalSegnoSegnoAlDoubleCoda", 3, "TargetSegnoSegno"),
    ("JumpDalSegnoSegnoAlFine", 4, "TargetSegnoSegno"),
)
# state -> (jump-to-coda direction, coda target marker)
_DA_CODA_BY_STATE = {2: ("JumpDaCoda", "TargetCoda"), 3: ("JumpDaDoubleCoda", "TargetDoubleCoda")}


def _mask(alternate_endings):
    mask = 0
    for ending in alternate_endings or ():
        if isinstance(ending, int) and 1 <= ending <= MAX_ALTERNATE_ENDING:
            mask |= 1 << (ending - 1)
    return mask


def build_repeat_groups(bars):
    """Bracket-match repeat_open/repeat_close_total_passes into groups.

    Returns (groups, diagnostics). `groups` is a list of
    {"opening_index", "closing_index", "total_passes"} using 0-based
    indices into `bars`, in closing order. `groups` is None (with a
    diagnostic) if any `repeat_open` is never closed by end of song.

    An unmatched `repeat_close` always pairs with the innermost still-open
    `repeat_open` (standard LIFO bracket matching, so nesting is exact); if
    none is open it implicitly opens at the start of the song, or right
    after the previous group resolved at the same nesting level (adjacent
    repeats).
    """
    groups = []
    stack = []
    base_next_open = 0
    for i, bar in enumerate(bars):
        if bar.get("repeat_open"):
            stack.append(i)
        total = bar.get("repeat_close_total_passes")
        if total:
            opening_index = stack.pop() if stack else base_next_open
            groups.append({"opening_index": opening_index, "closing_index": i,
                           "total_passes": total})
            if not stack:
                base_next_open = i + 1
    if stack:
        number = bars[stack[0]].get("number", stack[0] + 1)
        return None, [f"unclosed repeat_open at written bar {number}"]
    return groups, []


class _RepeatFrame:
    __slots__ = ("opening_index", "closing_index", "total_passes", "iteration")

    def __init__(self, group):
        self.opening_index = group["opening_index"]
        self.closing_index = group["closing_index"]
        self.total_passes = group["total_passes"]
        self.iteration = 0


def _find_jump_target(bars, target, search_index, backwards_first):
    def forwards():
        for i in range(search_index, len(bars)):
            if target in (bars[i].get("directions") or ()):
                return i
        return -1

    def backwards():
        for i in range(search_index, -1, -1):
            if target in (bars[i].get("directions") or ()):
                return i
        return -1

    if backwards_first:
        found = backwards()
        return found if found != -1 else forwards()
    found = forwards()
    return found if found != -1 else backwards()


def expand_performed_order(bars, max_steps=MAX_STEPS, max_output=MAX_OUTPUT_BARS):
    """Expand normalized written-bar facts into a bounded performed order.

    `bars` is an ordered list of dicts, one per written bar, each with:
      - number (int): the written bar number (1-indexed, source-stable)
      - repeat_open (bool)
      - repeat_close_total_passes (int or None): normalized TOTAL pass count
        for the repeat this bar closes (>=1), or None/0 if this bar does
        not close a repeat
      - alternate_endings (list[int]): explicit 1-indexed pass numbers this
        bar belongs to; empty means "every pass" unless a previous bar in
        the same bracket set an explicit mask, in which case it is inherited
      - directions (list[str]): zero or more of TARGET_DIRECTIONS |
        JUMP_DIRECTIONS

    Returns {"bars": [written bar numbers] | None, "status": "ok" |
    "unsupported", "diagnostics": [str], "bounded": bool}.
    """
    groups, group_diagnostics = build_repeat_groups(bars)
    if groups is None:
        return {"bars": None, "status": "unsupported",
                "diagnostics": group_diagnostics, "bounded": False}

    group_by_opening = {g["opening_index"]: g for g in groups}
    diagnostics = []
    output = []
    repeat_stack = []
    groups_on_stack = set()
    previous_alternate_endings = 0
    jumped_directions = set()
    state = 0
    index = 0
    steps = 0
    n = len(bars)

    while index < n:
        steps += 1
        if steps > max_steps or len(output) > max_output:
            diagnostics.append(
                "performed-order expansion exceeded the bounded step/output "
                "limit (possible runaway repeat/jump structure); aborting "
                "without a guessed result")
            return {"bars": None, "status": "unsupported",
                    "diagnostics": diagnostics, "bounded": True}

        bar = bars[index]
        should_play = True
        if state == 0:
            mask = _mask(bar.get("alternate_endings"))
            if mask == 0:
                mask = previous_alternate_endings
            group = group_by_opening.get(index)
            if group is not None and group["opening_index"] not in groups_on_stack:
                repeat_stack.append(_RepeatFrame(group))
                groups_on_stack.add(group["opening_index"])
                previous_alternate_endings = 0
                mask = _mask(bar.get("alternate_endings"))
            if not repeat_stack or mask == 0:
                should_play = True
            else:
                iteration = repeat_stack[-1].iteration
                previous_alternate_endings = mask
                should_play = bool(mask & (1 << iteration))
        if should_play:
            output.append(bar["number"])

        moved = _move_next_with_directions(bars, index, state, jumped_directions, diagnostics)
        if moved is not None:
            index, state, did_jump = moved
            if did_jump:
                repeat_stack = []
                groups_on_stack = set()
                previous_alternate_endings = 0
            continue
        index, state, repeat_stack, groups_on_stack, previous_alternate_endings = \
            _move_next_with_normal_repeats(bars, index, state, repeat_stack,
                                           groups_on_stack, previous_alternate_endings)

    return {"bars": output, "status": "ok", "diagnostics": diagnostics, "bounded": False}


def _move_next_with_directions(bars, index, state, jumped_directions, diagnostics):
    """Mirrors `MidiPlaybackController._moveNextWithDirections`.

    Returns None if directions do not change control flow this step (the
    caller should fall through to normal repeat handling), otherwise
    (new_index, new_state, did_jump).
    """
    bar = bars[index]
    directions = set(bar.get("directions") or ())
    has_directions = bool(directions)
    if state == 0 and not has_directions:
        return None
    if not has_directions:
        return index + 1, state, False

    if state == 0:
        for name, new_state in _DA_CAPO_JUMPS:
            if name in directions and (index, name) not in jumped_directions:
                jumped_directions.add((index, name))
                return 0, new_state, True
        for name, new_state, target in _DAL_SEGNO_JUMPS:
            if name in directions and (index, name) not in jumped_directions:
                found = _find_jump_target(bars, target, index, True)
                if found == -1:
                    diagnostics.append(
                        f"navigation jump '{name}' at written bar {bar['number']} has "
                        f"no reachable '{target}' marker; jump ignored (matches "
                        "reference playback fallback)")
                    continue
                jumped_directions.add((index, name))
                return found, new_state, True
        return None
    if state == 1:
        return index + 1, state, False
    if state in (2, 3):
        jump_name, target = _DA_CODA_BY_STATE[state]
        if jump_name in directions:
            found = _find_jump_target(bars, target, index, False)
            if found == -1:
                diagnostics.append(
                    f"navigation jump '{jump_name}' at written bar {bar['number']} has "
                    f"no reachable '{target}' marker; jump ignored (matches reference "
                    "playback fallback)")
                return index + 1, state, False
            return found, 0, False
        return index + 1, state, False
    if state == 4:
        if "TargetFine" in directions:
            return len(bars), state, False
        return index + 1, state, False
    return index + 1, state, False


def _move_next_with_normal_repeats(bars, index, state, repeat_stack, groups_on_stack,
                                   previous_alternate_endings):
    """Mirrors `MidiPlaybackController._moveNextWithNormalRepeats`."""
    bar = bars[index]
    total_passes = bar.get("repeat_close_total_passes") or 0
    extra_repeats = total_passes - 1 if total_passes else 0
    if repeat_stack and extra_repeats > 0:
        frame = repeat_stack[-1]
        if frame.iteration < extra_repeats:
            new_index = frame.opening_index
            frame.iteration += 1
            previous_alternate_endings = 0
            return new_index, state, repeat_stack, groups_on_stack, previous_alternate_endings
        repeat_stack = repeat_stack[:-1]
        groups_on_stack = groups_on_stack - {frame.opening_index}
        return index + 1, state, repeat_stack, groups_on_stack, previous_alternate_endings
    return index + 1, state, repeat_stack, groups_on_stack, previous_alternate_endings
