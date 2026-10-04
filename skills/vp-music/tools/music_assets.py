"""Bounded, read-only music-file inspection. Stage downloads before calling this tool."""

import argparse
from collections import deque
from fractions import Fraction
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import struct
import sys
import xml.etree.ElementTree as ET
import zipfile

from performed_order import expand_performed_order

MAX_FILE = 16 * 1024 * 1024
MAX_XML = 8 * 1024 * 1024
MAX_ENTRIES = 128
MAX_EVENTS = 200_000
MAX_OBJECTS = 100_000


class Invalid(ValueError):
    pass


class Unsupported(Invalid):
    pass


def _require(condition, reason):
    if not condition:
        raise Invalid(reason)


def _number(text, default=None):
    try:
        return int(text)
    except (ValueError, TypeError):
        return default


def _fraction(value):
    return float(value)


def bar_seconds(start, end, tempos):
    """Integrate quarter-note positions over absolute (quarter position, BPM) changes."""
    _require(end > start, "bar end must follow its start")
    ordered = sorted((Fraction(pos), Fraction(bpm)) for pos, bpm in tempos)
    _require(ordered and ordered[0][0] <= start and all(b > 0 for _, b in ordered),
             "bar requires a positive tempo at its start")
    bpm = ordered[0][1]
    for pos, value in ordered:
        if pos <= start:
            bpm = value
    total = Fraction(0)
    cursor = start
    for pos, value in ordered:
        if start < pos < end:
            total += (pos - cursor) * 60 / bpm
            cursor = pos
            bpm = value
    return _fraction(total + (end - cursor) * 60 / bpm)


def _bars(meters, end, tempos, assumed_meter_until=None, assumed_tempo_until=None):
    """Meter changes open a new bar; positions and durations are quarter notes."""
    meters = sorted(meters)
    _require(meters and meters[0][0] == 0, "missing initial meter")
    result = []
    pos = Fraction(0)
    index = 0
    while pos < end:
        _require(len(result) < MAX_OBJECTS, "too many bars")
        while index + 1 < len(meters) and meters[index + 1][0] <= pos:
            index += 1
        numerator, denominator = meters[index][1:]
        _require(1 <= numerator <= 64 and denominator in (1, 2, 4, 8, 16, 32),
                 "invalid meter")
        stop = pos + Fraction(numerator * 4, denominator)
        if index + 1 < len(meters):
            stop = min(stop, meters[index + 1][0])
        stop = min(stop, end)
        bar = {"number": len(result) + 1, "start_quarters": _fraction(pos),
               "length_quarters": _fraction(stop - pos),
               "meter": f"{numerator}/{denominator}",
               "duration_seconds": bar_seconds(pos, stop, tempos)}
        if assumed_meter_until is not None:
            bar["meter_assumed"] = pos < assumed_meter_until
        if assumed_tempo_until is not None:
            bar["duration_seconds_assumed"] = pos < assumed_tempo_until
        result.append(bar)
        pos = stop
    return result


def _vlq(data, pos, limit):
    value = 0
    for _ in range(4):
        _require(pos < limit, "truncated MIDI variable-length quantity")
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 127)
        if byte < 128:
            return value, pos
    raise Invalid("oversized MIDI variable-length quantity")


def _midi(data):
    _require(len(data) >= 14 and data[:4] == b"MThd", "invalid MIDI header")
    length = int.from_bytes(data[4:8], "big")
    _require(length >= 6 and 8 + length <= len(data), "truncated MIDI header")
    fmt, count, division = struct.unpack_from(">HHH", data, 8)
    _require(fmt in (0, 1) and 1 <= count <= 256 and (fmt != 0 or count == 1),
             "unsupported MIDI track format")
    _require(division and not division & 0x8000, "SMPTE MIDI timing unsupported")
    pos = 8 + length
    tracks, tempos, meters = [], [(Fraction(0), 120)], [(Fraction(0), 4, 4)]
    explicit_tempos, explicit_meters = [], []
    max_tick = 0
    events = 0
    for track_index in range(count):
        _require(data[pos:pos + 4] == b"MTrk" and pos + 8 <= len(data),
                 "missing MIDI track chunk")
        size = int.from_bytes(data[pos + 4:pos + 8], "big")
        pos += 8
        limit = pos + size
        _require(limit <= len(data), "truncated MIDI track")
        tick, running, ended = 0, None, False
        notes, active = [], {}
        name = f"Track {track_index + 1}"
        while pos < limit:
            events += 1
            _require(events <= MAX_EVENTS, "too many MIDI events")
            delta, pos = _vlq(data, pos, limit)
            tick += delta
            _require(tick <= 100_000_000, "MIDI timeline too long")
            _require(pos < limit, "truncated MIDI event")
            status = data[pos]
            if status & 0x80:
                pos += 1
                if status < 0xF0:
                    running = status
            else:
                _require(running is not None, "MIDI running status without status byte")
                status = running
            if status in (0xFF, 0xF0, 0xF7):
                running = None
                if status == 0xFF:
                    _require(pos < limit, "truncated MIDI meta event")
                    kind = data[pos]
                    pos += 1
                size, pos = _vlq(data, pos, limit)
                _require(pos + size <= limit, "truncated MIDI event payload")
                payload = data[pos:pos + size]
                pos += size
                if status == 0xFF:
                    if kind == 0x51:
                        _require(size == 3 and int.from_bytes(payload, "big") > 0,
                                 "invalid MIDI tempo")
                        tempos.append((Fraction(tick, division),
                                       Fraction(60_000_000, int.from_bytes(payload, "big"))))
                        explicit_tempos.append(Fraction(tick, division))
                    elif kind == 0x58:
                        _require(size >= 2 and payload[1] <= 5, "invalid MIDI meter")
                        meters.append((Fraction(tick, division), payload[0], 2 ** payload[1]))
                        explicit_meters.append(Fraction(tick, division))
                    elif kind == 0x03:
                        name = payload.decode("latin-1", errors="replace")[:200]
                    elif kind == 0x2F:
                        _require(size == 0 and pos == limit, "invalid MIDI end-of-track")
                        ended = True
                continue
            _require(0x80 <= status <= 0xEF, "unsupported MIDI status")
            width = 1 if status >> 4 in (0xC, 0xD) else 2
            _require(pos + width <= limit and all(b < 128 for b in data[pos:pos + width]),
                     "invalid MIDI channel event")
            a = data[pos]
            b = data[pos + 1] if width == 2 else 0
            pos += width
            channel = status & 15
            key = (channel, a)
            if status >> 4 == 0x9 and b:
                active.setdefault(key, deque()).append((tick, b))
            elif status >> 4 == 0x8 or (status >> 4 == 0x9 and not b):
                if active.get(key):
                    begin, velocity = active[key].popleft()
                    notes.append({"pitch": a, "velocity": velocity, "channel": channel,
                                  "start_quarters": begin / division,
                                  "duration_quarters": (tick - begin) / division})
        _require(ended, "MIDI track missing end_of_track")
        _require(not any(active.values()), "unclosed MIDI notes")
        max_tick = max(max_tick, tick)
        tracks.append({"name": name, "notes": notes})
    _require(pos == len(data), "unexpected data after MIDI tracks")
    # Events at the same tick use the last event in track order.
    tempos = list(dict(tempos).items())
    meters = [(p, n, d) for p, (n, d) in
              dict((p, (n, d)) for p, n, d in meters).items()]
    tempo_assumed_until = (min(explicit_tempos) if explicit_tempos
                           else Fraction(max_tick, division) + 1)
    meter_assumed_until = (min(explicit_meters) if explicit_meters
                           else Fraction(max_tick, division) + 1)
    bars = _bars(meters, Fraction(max_tick, division), tempos,
                 meter_assumed_until if 0 not in explicit_meters else None,
                 tempo_assumed_until if 0 not in explicit_tempos else None)
    for track in tracks:
        for note in track["notes"]:
            note["bar"] = next((bar["number"] for bar in bars
                                if bar["start_quarters"] <= note["start_quarters"]
                                < bar["start_quarters"] + bar["length_quarters"]), None)
    missing = []
    if 0 not in explicit_tempos:
        missing.append("initial MIDI tempo not specified (120 BPM assumed for duration)")
    if 0 not in explicit_meters:
        missing.append("initial MIDI meter not specified (4/4 assumed for bars)")
    missing.append("guitar tablature (string, fret, tuning, capo not encoded in MIDI)")
    for track in tracks:
        track["tablature"] = {"status": "unavailable", "string_numbering": None,
                              "missing_fields": ["note string", "note fret", "tuning", "capo"]}
    return tracks, bars, [{"position_quarters": float(p), "bpm": float(b),
                           "assumed": p == 0 and 0 not in explicit_tempos}
                          for p, b in sorted(tempos)], missing


def _beat_quarters(duration):
    """Quarter-note length of a single PyGuitarPro beat duration object."""
    quarter = Fraction(4, duration.value)
    if duration.isDotted:
        quarter *= Fraction(3, 2)
    quarter *= Fraction(duration.tuplet.times, duration.tuplet.enters)
    return quarter


# PyGuitarPro's gp3/gp5 reader normalizes the GP5-only direction/fromDirection
# marker text (see guitarpro/gp5.py readDirections) to these exact English
# labels; map them to the same canonical names GPIF uses
# (<Directions><Target>/<Jump>...) so both formats share one vocabulary.
LEGACY_DIRECTION_NAMES = {
    "Coda": "TargetCoda", "Double Coda": "TargetDoubleCoda", "Segno": "TargetSegno",
    "Segno Segno": "TargetSegnoSegno", "Fine": "TargetFine",
    "Da Capo": "JumpDaCapo", "Da Capo al Coda": "JumpDaCapoAlCoda",
    "Da Capo al Double Coda": "JumpDaCapoAlDoubleCoda", "Da Capo al Fine": "JumpDaCapoAlFine",
    "Da Segno": "JumpDalSegno", "Da Segno al Coda": "JumpDalSegnoAlCoda",
    "Da Segno al Double Coda": "JumpDalSegnoAlDoubleCoda", "Da Segno al Fine": "JumpDalSegnoAlFine",
    "Da Segno Segno": "JumpDalSegnoSegno", "Da Segno Segno al Coda": "JumpDalSegnoSegnoAlCoda",
    "Da Segno Segno al Double Coda": "JumpDalSegnoSegnoAlDoubleCoda",
    "Da Segno Segno al Fine": "JumpDalSegnoSegnoAlFine",
    "Da Coda": "JumpDaCoda", "Da Double Coda": "JumpDaDoubleCoda",
}


def _legacy(data):
    try:
        import guitarpro
    except ImportError as exc:
        raise Invalid("PyGuitarPro missing; install requirements.txt") from exc
    from io import BytesIO
    try:
        song = guitarpro.parse(BytesIO(data))
        _require(len(song.tracks) <= 256 and len(song.measureHeaders) <= 10_000,
                 "GP score exceeds limits")
        _require(len(song.tracks) * len(song.measureHeaders) <= MAX_OBJECTS,
                 "too many GP track measures")
        tempos = [(Fraction(0), song.tempo)]
        bars = []
        for header in song.measureHeaders:
            start = Fraction(header.start - song.measureHeaders[0].start, 960)
            meter = header.timeSignature
            length = Fraction(4 * meter.numerator, meter.denominator.value)
            # guitarpro normalizes repeatClose to "extra repeats after the
            # first playthrough" uniformly across gp3/gp4/gp5 (gp5.py
            # subtracts 1 from the raw, already-total byte read from file;
            # gp3/gp4 store that same "extra repeats" meaning directly), so
            # the normalized TOTAL pass count is always repeatClose + 1.
            total_passes = header.repeatClose + 1 if header.repeatClose >= 0 else None
            alternate_endings = [i + 1 for i in range(8) if header.repeatAlternative & (1 << i)]
            directions = sorted(filter(None, (
                LEGACY_DIRECTION_NAMES.get(header.direction.name) if header.direction else None,
                LEGACY_DIRECTION_NAMES.get(header.fromDirection.name) if header.fromDirection else None,
            )))
            bars.append({"number": header.number, "start_quarters": float(start),
                         "length_quarters": float(length),
                         "meter": f"{meter.numerator}/{meter.denominator.value}",
                         "repeat_open": header.isRepeatOpen,
                         "repeat_close": header.repeatClose if header.repeatClose > 0 else None,
                         "repeat_alternative": header.repeatAlternative or None,
                         "repeat_close_total_passes": total_passes,
                         "alternate_endings": alternate_endings,
                         "directions": directions,
                         "anacrusis": False, "anacrusis_actual_length_quarters": None})
        # Legacy GP has no explicit anacrusis flag (unlike GPIF's
        # <MasterTrack><Anacrusis/>); infer a pickup first bar from any
        # track whose first measure's written content is shorter than its
        # nominal meter length.
        if bars and song.tracks and song.tracks[0].measures:
            first_measure = song.tracks[0].measures[0]
            actual = sum((_beat_quarters(beat.duration)
                         for voice in first_measure.voices for beat in voice.beats),
                        Fraction(0))
            nominal = Fraction(str(bars[0]["length_quarters"]))
            if 0 < actual < nominal:
                bars[0]["anacrusis"] = True
                bars[0]["anacrusis_actual_length_quarters"] = float(actual)
        tracks = []
        visited = 0
        for track in song.tracks:
            notes = []
            for measure in track.measures:
                for voice in measure.voices:
                    for beat in voice.beats:
                        visited += 1
                        _require(visited <= MAX_EVENTS, "too many GP beats")
                        pos = Fraction(beat.start - song.measureHeaders[0].start, 960)
                        change = beat.effect.mixTableChange
                        if change and change.tempo and change.tempo.value > 0:
                            tempos.append((pos, change.tempo.value))
                        quarter = _beat_quarters(beat.duration)
                        for note in beat.notes:
                            pitch = (track.strings[note.string - 1].value + note.value
                                     if 1 <= note.string <= len(track.strings) else None)
                            notes.append({"bar": measure.header.number, "pitch": pitch,
                                          "string": note.string, "fret": note.value,
                                          "start_quarters": float(pos),
                                          "duration_quarters": float(quarter)})
                            _require(len(notes) <= MAX_OBJECTS, "too many GP notes")
            tuning = [string.value for string in track.strings]
            gaps = []
            if not tuning or any(not 0 <= value <= 127 for value in tuning):
                gaps.append("tuning")
            if any(note["pitch"] is None or note["string"] < 1 or
                   note["string"] > len(tuning) or note["fret"] < 0 for note in notes):
                gaps.append("note string/fret mapping")
            if not notes:
                gaps.append("no tab notes")
            tracks.append({
                "name": track.name, "notes": notes,
                "staves": [{"number": 1, "tuning_midi": tuning,
                            "capo_fret": track.offset}],
                "tablature": {
                    "status": "complete" if not gaps else "unavailable",
                    "string_numbering": "1=highest-pitched string",
                    "missing_fields": gaps,
                },
            })
        tempos = sorted(dict(tempos).items())
        for bar in bars:
            a = Fraction(str(bar["start_quarters"]))
            b = a + Fraction(str(bar["length_quarters"]))
            bar["duration_seconds"] = bar_seconds(a, b, tempos)
        return tracks, bars, [{"position_quarters": float(p), "bpm": float(v)}
                              for p, v in tempos], []
    except (guitarpro.GPException, IndexError, ValueError, TypeError, AttributeError,
            ZeroDivisionError, EOFError, struct.error, OverflowError, UnicodeError) as exc:
        raise Invalid(f"malformed legacy GP: {exc}") from exc


def _archive(data):
    from io import BytesIO
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            _require(len(entries) <= MAX_ENTRIES, "too many archive entries")
            seen = set()
            for entry in entries:
                path = PurePosixPath(entry.filename)
                _require(not entry.filename.startswith(("/", "\\")) and
                         "\\" not in entry.filename and ":" not in entry.filename and
                         ".." not in path.parts and entry.filename not in seen,
                         "unsafe archive path or duplicate entry")
                seen.add(entry.filename)
                _require(not entry.flag_bits & 1 and entry.file_size <= MAX_XML and
                         entry.file_size <= max(entry.compress_size, 1) * 200 and
                         entry.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
                         "unsafe archive member")
            _require(sum(item.file_size for item in entries) <= MAX_FILE,
                     "archive expands beyond limit")
            _require("Content/score.gpif" in seen, "GP archive lacks Content/score.gpif")
            xml = archive.read("Content/score.gpif")
    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError, NotImplementedError) as exc:
        raise Invalid(f"invalid GP archive: {exc}") from exc
    _require(len(xml) <= MAX_XML and b"<!DOCTYPE" not in xml.upper() and
             b"<!ENTITY" not in xml.upper(), "unsafe GPIF XML")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise Invalid(f"malformed GPIF XML: {exc}") from exc
    _require(root.tag == "GPIF" and sum(1 for _ in root.iter()) <= MAX_OBJECTS,
             "invalid or oversized GPIF score")
    return _gpif(root)


def _tablature_status(tracks, missing):
    incomplete = []
    for track in tracks:
        gaps = set()
        if not track["notes"]:
            gaps.add("no tab notes")
        staves = track.get("staves", [])
        if not staves:
            gaps.add("staff tuning and capo")
        for staff in staves:
            if staff["tuning_midi"] is None:
                gaps.add("tuning")
            if staff["capo_fret"] is None:
                gaps.add("capo")
            if staff.get("percussion"):
                gaps.add("percussion is not guitar tablature")
        for note in track["notes"]:
            if note.get("string") is None:
                gaps.add("note string")
            elif (note.get("staff") is None or note["staff"] > len(staves) or
                  (staves[note["staff"] - 1]["tuning_midi"] is not None and
                   note["string"] > len(staves[note["staff"] - 1]["tuning_midi"]))):
                gaps.add("note string/staff mapping")
            if note.get("fret") is None:
                gaps.add("note fret")
        usable = any(
            note.get("string") is not None and note.get("fret") is not None and
            isinstance(note.get("staff"), int) and
            1 <= note["staff"] <= len(staves) and
            staves[note["staff"] - 1]["tuning_midi"] is not None and
            staves[note["staff"] - 1]["capo_fret"] is not None and
            not staves[note["staff"] - 1].get("percussion") and
            note["string"] <= len(staves[note["staff"] - 1]["tuning_midi"])
            for note in track["notes"])
        track["tablature"] = {
            "status": "complete" if not gaps else "partial" if usable else "unavailable",
            "string_numbering": "1=lowest-pitched string",
            "missing_fields": sorted(gaps),
        }
        if gaps:
            incomplete.append(track["name"])
    if incomplete:
        missing.append("accurate tablature unavailable for some tracks: " +
                       ", ".join(incomplete[:20]) + ("..." if len(incomplete) > 20 else ""))


def _gpif(root):
    masters = root.findall("./MasterBars/MasterBar")
    _require(0 < len(masters) <= 10_000, "missing or excessive GPIF master bars")
    tracks_xml = root.findall("./Tracks/Track")
    _require(0 < len(tracks_xml) <= 256, "missing or excessive GPIF tracks")
    automations = root.findall("./MasterTrack/Automations/Automation")
    tempo_by_bar = {}
    missing = []
    for item in automations:
        if item.findtext("Type") == "Tempo":
            bar = _number(item.findtext("Bar"))
            position = _number(item.findtext("Position"))
            bpm = _number((item.findtext("Value") or "").split()[0] if
                          (item.findtext("Value") or "").split() else None)
            if bar is not None and position == 0 and bpm and bpm > 0:
                tempo_by_bar[bar] = bpm
            else:
                missing.append("non-barline or unrecognized GPIF tempo automation")
    # <MasterTrack><Anacrusis/> (if present) always marks only the very
    # first master bar as a pickup, per alphaTab's GPIF importer.
    has_anacrusis = root.find("./MasterTrack/Anacrusis") is not None
    bars, tempos, cursor = [], [], Fraction(0)
    for i, master in enumerate(masters):
        match = re.fullmatch(r"(\d+)/(\d+)", master.findtext("Time") or "")
        _require(match is not None, "invalid GPIF meter")
        num, den = map(int, match.groups())
        _require(1 <= num <= 64 and den in (1, 2, 4, 8, 16, 32), "invalid GPIF meter")
        if i in tempo_by_bar:
            tempos.append((cursor, tempo_by_bar[i]))
        length = Fraction(num * 4, den)
        repeat = master.find("Repeat")
        repeat_open = (repeat.get("start", "").lower() == "true" or
                       repeat.find("Start") is not None) if repeat is not None else False
        # GPIF's Repeat@count is already the normalized TOTAL pass count
        # (alphaTab's GpifParser reads it directly into `repeatCount` with
        # no adjustment), unlike legacy GP's repeatClose.
        repeat_close = (_number(repeat.get("count")) or _number(repeat.findtext("Count"))
                        if repeat is not None and
                        (repeat.get("end", "").lower() == "true" or
                         repeat.find("End") is not None) else None)
        endings_text = master.findtext("AlternateEndings")
        alternate_endings = sorted({n for n in (_number(t) for t in (endings_text or "").split())
                                    if n is not None and 1 <= n <= 8})
        directions = sorted(
            f"{child.tag}{(child.text or '').strip()}"
            for child in master.findall("./Directions/*")
            if child.tag in ("Target", "Jump") and (child.text or "").strip())
        bars.append({"number": i + 1, "start_quarters": float(cursor),
                     "length_quarters": float(length), "meter": f"{num}/{den}",
                     "repeat_open": repeat_open, "repeat_close": repeat_close,
                     "repeat_close_total_passes": repeat_close,
                     "alternate_endings": alternate_endings, "directions": directions,
                     "anacrusis": i == 0 and has_anacrusis,
                     "anacrusis_actual_length_quarters": float(length) if i == 0 and has_anacrusis else None})
        cursor += length
    if not tempos or tempos[0][0] != 0:
        missing.append("initial tempo")
    lookup = {}
    for tag, singular in (("Bars", "Bar"), ("Voices", "Voice"), ("Beats", "Beat"),
                          ("Notes", "Note"), ("Rhythms", "Rhythm")):
        lookup[tag] = {item.get("id"): item for item in root.findall(f"./{tag}/{singular}")}
    rhythm_lengths = {"Whole": 4, "Half": 2, "Quarter": 1, "Eighth": Fraction(1, 2),
                      "16th": Fraction(1, 4), "32nd": Fraction(1, 8),
                      "64th": Fraction(1, 16)}
    tracks = []
    for i, item in enumerate(tracks_xml):
        color_parts = [_number(p) for p in (item.findtext("Color") or "").split()]
        source_color = (f"#{color_parts[0]:02X}{color_parts[1]:02X}{color_parts[2]:02X}"
                        if len(color_parts) >= 3 and
                        all(isinstance(n, int) and 0 <= n <= 255 for n in color_parts[:3])
                        else None)
        midi = item.find("./Sounds/Sound/MIDI")
        program = _number(midi.findtext("Program")) if midi is not None else None
        lsb = _number(midi.findtext("LSB")) if midi is not None else None
        msb = _number(midi.findtext("MSB")) if midi is not None else None
        connection = item.find("MidiConnection")
        primary = _number(connection.findtext("PrimaryChannel")) if connection is not None else None
        secondary = _number(connection.findtext("SecondaryChannel")) if connection is not None else None
        instrument_type = (item.findtext("./InstrumentSet/Type") or "").lower()
        percussion = instrument_type in ("drums", "percussion")
        staves = []
        for j, staff in enumerate(item.findall("./Staves/Staff")):
            pitches = staff.findtext('./Properties/Property[@name="Tuning"]/Pitches')
            numbers = [_number(p) for p in pitches.split()] if pitches else []
            tuning = (numbers if numbers and len(numbers) <= 16 and
                      all(n is not None and 0 <= n <= 127 for n in numbers) and
                      any(numbers) else None)
            capo = _number(staff.findtext('./Properties/Property[@name="CapoFret"]/Fret'))
            staves.append({"number": j + 1, "tuning_midi": tuning,
                           "capo_fret": capo if capo is not None and 0 <= capo <= 36 else None,
                           "percussion": percussion})
        tracks.append({"name": item.findtext("Name") or f"Track {i + 1}",
                       "playback": {
                           "program": program,
                           "bank": (msb * 128 + lsb if isinstance(msb, int) and
                                    isinstance(lsb, int) else None),
                           "primary_channel": primary,
                           "secondary_channel": secondary,
                       },
                       "source_color": source_color,
                       "staves": staves, "notes": []})
    visited = 0
    for bar_index, master in enumerate(masters):
        bar_refs = (master.findtext("Bars") or "").split()
        _require(len(bar_refs) == len(tracks), "GPIF track/bar mapping inconsistent")
        for track, bar_ref in zip(tracks, bar_refs):
            visited += 1
            _require(visited <= MAX_OBJECTS, "too many GPIF bar references")
            bar = lookup["Bars"].get(bar_ref)
            _require(bar is not None, "GPIF references missing bar")
            for voice_ref in (bar.findtext("Voices") or "").split():
                if voice_ref == "-1":
                    continue
                voice = lookup["Voices"].get(voice_ref)
                _require(voice is not None, "GPIF references missing voice")
                offset = Fraction(0)
                for beat_ref in (voice.findtext("Beats") or "").split():
                    visited += 1
                    _require(visited <= MAX_OBJECTS, "too many GPIF beat references")
                    beat = lookup["Beats"].get(beat_ref)
                    _require(beat is not None, "GPIF references missing beat")
                    rhythm_node = beat.find("Rhythm")
                    rhythm = lookup["Rhythms"].get(rhythm_node.get("ref") if rhythm_node is not None else None)
                    value = rhythm.findtext("NoteValue") if rhythm is not None else None
                    duration = rhythm_lengths.get(value)
                    _require(duration is not None, "unknown GPIF rhythm")
                    dot = rhythm.find("AugmentationDot")
                    if dot is not None:
                        dots = _number(dot.get("count"))
                        _require(dots is not None and 0 <= dots <= 3, "invalid GPIF dot")
                        duration *= sum((Fraction(1, 2 ** j) for j in range(dots + 1)), Fraction(0))
                    tuplet = rhythm.find("PrimaryTuplet")
                    if tuplet is not None:
                        n, d = _number(tuplet.get("num")), _number(tuplet.get("den"))
                        _require(n and d and 0 < n <= 64 and 0 < d <= 64, "invalid GPIF tuplet")
                        duration *= Fraction(d, n)
                    for note_ref in (beat.findtext("Notes") or "").split():
                        note = lookup["Notes"].get(note_ref)
                        _require(note is not None, "GPIF references missing note")
                        props = {p.get("name"): p for p in note.findall("./Properties/Property")}
                        pitch = _number(props["Midi"].findtext("Number")) if "Midi" in props else None
                        raw_string = (_number(props["String"].findtext("String"))
                                      if "String" in props else None)
                        fret = _number(props["Fret"].findtext("Fret")) if "Fret" in props else None
                        if pitch is None:
                            missing.append("some GPIF notes lack MIDI pitch")
                        track["notes"].append({"bar": bar_index + 1, "pitch": pitch,
                                               "staff": 1, "source_string": raw_string,
                                               "string": raw_string + 1 if raw_string is not None and raw_string >= 0 else None,
                                               "fret": fret if fret is not None and fret >= 0 else None,
                                               "start_quarters": float(Fraction(str(bars[bar_index]["start_quarters"])) + offset),
                                               "duration_quarters": float(duration)})
                        _require(len(track["notes"]) <= MAX_OBJECTS, "too many GPIF notes")
                    offset += duration
    if tempos and tempos[0][0] == 0 and not any("tempo automation" in x for x in missing):
        for bar in bars:
            pos = Fraction(str(bar["start_quarters"]))
            bar["duration_seconds"] = bar_seconds(pos, pos + Fraction(str(bar["length_quarters"])), tempos)
    else:
        missing.append("bar duration seconds (tempo unavailable or incomplete)")
        for bar in bars:
            bar["duration_seconds"] = None
    _tablature_status(tracks, missing)
    return tracks, bars, [{"position_quarters": float(p), "bpm": float(b)}
                          for p, b in tempos], sorted(set(missing))


def _gpx(data):
    _require(len(data) >= 8 and 0 < int.from_bytes(data[4:8], "little") <= MAX_XML,
             "invalid or oversized GPX BCFZ header")
    script = Path(__file__).with_name("gpx_extract.mjs")
    try:
        process = subprocess.run(["node", "--max-old-space-size=128", str(script)],
                                 input=data, capture_output=True, timeout=20, check=False)
    except FileNotFoundError as exc:
        raise Unsupported("GPX requires Node.js and npm install in tools") from exc
    except subprocess.TimeoutExpired as exc:
        raise Invalid("GPX parsing exceeded time limit") from exc
    if process.returncode:
        message = process.stderr.decode("utf-8", errors="replace")[:300]
        if "ERR_MODULE_NOT_FOUND" in message or "Cannot find package" in message:
            raise Unsupported("GPX requires npm install in tools (@coderline/alphatab)")
        raise Invalid(f"malformed or excessive GPX: {message}")
    _require(len(process.stdout) <= MAX_FILE, "GPX score expands beyond result limit")
    try:
        score = json.loads(process.stdout)
    except (ValueError, UnicodeError) as exc:
        raise Invalid("GPX parser returned invalid score") from exc
    tracks, bars, tempos = score["tracks"], score["bars"], score["tempos"]
    _require(0 < len(tracks) <= 256 and 0 < len(bars) <= 10_000 and
             sum(len(track["notes"]) for track in tracks) <= MAX_OBJECTS,
             "GPX score exceeds limits")
    missing = []
    if not tempos or tempos[0]["position_quarters"] != 0:
        missing.append("initial GPX tempo unavailable")
    if any(note["pitch"] is None for track in tracks for note in track["notes"]):
        missing.append("some GPX notes lack MIDI pitch")
    _tablature_status(tracks, missing)
    if "initial GPX tempo unavailable" not in missing:
        changes = [(Fraction(str(t["position_quarters"])), Fraction(str(t["bpm"])))
                   for t in tempos]
        for bar in bars:
            start = Fraction(str(bar["start_quarters"]))
            bar["duration_seconds"] = bar_seconds(
                start, start + Fraction(str(bar["length_quarters"])), changes)
    else:
        missing.append("bar duration seconds (tempo unavailable)")
        for bar in bars:
            bar["duration_seconds"] = None
    return tracks, bars, tempos, missing


def inspect(path, include_notes=True, require_notes=False):
    """Return JSON-compatible metadata; never modify the source file."""
    path = Path(path).resolve()
    result = {"source": str(path), "format": None, "status": "invalid",
              "confidence": "none", "missing_fields": [], "error": None,
              "checksum_sha256": None, "metadata": None, "performed_order": None}
    try:
        _require(path.is_file(), "not a regular file")
        _require(path.stat().st_size <= MAX_FILE, "file exceeds 16 MiB limit")
        data = path.read_bytes()
        _require(len(data) <= MAX_FILE, "file exceeds 16 MiB limit")
        result["checksum_sha256"] = hashlib.sha256(data).hexdigest()
        _require(data, "empty file")
        ext = path.suffix.lower()
        if data.startswith(b"MThd"):
            fmt = "midi"
            parser = _midi
        elif data.startswith(b"BCFZ"):
            fmt = "gpx"
            parser = _gpx
        elif data.startswith(b"PK\x03\x04"):
            fmt = "gp-zip"
            parser = _archive
        elif len(data) > 26 and data[1:19] == b"FICHIER GUITAR PRO":
            version = re.match(rb"FICHIER GUITAR PRO v([345])\.", data[1:32])
            _require(version is not None, "unsupported legacy GP version")
            fmt = f"gp{version.group(1).decode()}"
            parser = _legacy
        else:
            raise Invalid("unrecognized content (HTML and arbitrary downloads are not music files)")
        result["format"] = fmt
        _require(ext == {"midi": ".mid", "gp-zip": ".gp", "gpx": ".gpx"}.get(fmt, f".{fmt}") or
                 (fmt == "midi" and ext == ".midi"), "file extension does not match content")
        tracks, bars, tempos, missing = parser(data)
        note_count = sum(len(t["notes"]) for t in tracks)
        if note_count == 0:
            missing.append("note content (0 notes)")
        performed_order = expand_performed_order([
            {"number": bar["number"], "repeat_open": bar.get("repeat_open", False),
             "repeat_close_total_passes": bar.get("repeat_close_total_passes"),
             "alternate_endings": bar.get("alternate_endings") or [],
             "directions": bar.get("directions") or []}
            for bar in bars])
        if performed_order["status"] != "ok":
            missing.append("performed order not expanded: " +
                           "; ".join(performed_order["diagnostics"]))
        result.update(status="valid", confidence="parsed" if not missing else "partial",
                      missing_fields=missing, tracks=tracks, bars=bars, tempos=tempos,
                      track_count=len(tracks), bar_count=len(bars),
                      performed_order=performed_order,
                      metadata={"size_bytes": len(data), "track_count": len(tracks),
                                "bar_count": len(bars),
                                "note_count": note_count,
                                "meters": sorted(set(b["meter"] for b in bars)),
                                "tempo_changes": len(tempos)})
        if require_notes and note_count == 0:
            result.update(status="invalid", error="no note content; not a music analysis asset")
        if not include_notes:
            for track in tracks:
                track["note_count"] = len(track.pop("notes"))
    except Unsupported as exc:
        result.update(status="unsupported", error=str(exc),
                      missing_fields=["tracks", "bars", "notes", "tempo", "meter", "repeats"])
    except (Invalid, OSError, MemoryError, RecursionError) as exc:
        result["error"] = str(exc)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read-only JSON validation, analysis, and bar durations for staged MIDI/GP files "
                    "(GPX requires Node.js and npm ci inside tools)")
    commands = parser.add_subparsers(dest="command", required=True)
    helps = {"validate": "validate a staged local music file; emit JSON",
             "inspect": "extract analysis metadata and notes; emit JSON",
             "duration": "calculate bar durations without expanding repeats; emit JSON"}
    for name, description in helps.items():
        command = commands.add_parser(name, help=description)
        command.add_argument("--path", required=True, type=Path,
                             help="local music file; caller must stage downloads before validation")
    args = parser.parse_args(argv)
    result = inspect(args.path, include_notes=args.command == "inspect",
                     require_notes=args.command == "validate")
    if args.command == "duration":
        bars = result.pop("bars", [])
        result.pop("tracks", None)
        result.pop("tempos", None)
        result["bars"] = bars
        durations = [bar.get("duration_seconds") for bar in bars]
        result["total_seconds"] = (sum(durations) if durations and all(
            value is not None for value in durations) else None)
        result["repeat_expansion"] = "not applied"
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "valid" else 1


if __name__ == "__main__":
    sys.exit(main())
