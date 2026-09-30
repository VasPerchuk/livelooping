import io
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import unittest
import zipfile

from music_assets import bar_seconds, inspect


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def vlq(value):
    parts = [value & 127]
    value >>= 7
    while value:
        parts.insert(0, (value & 127) | 128)
        value >>= 7
    return bytes(parts)


def midi(events):
    body = b"".join(vlq(delta) + payload for delta, payload in events)
    return b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480) + b"MTrk" + struct.pack(">I", len(body)) + body


def gp_zip(xml, extra=None):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Content/score.gpif", xml)
        if extra:
            archive.writestr(extra, b"x")
    return data.getvalue()


class MusicToolTests(unittest.TestCase):
    def setUp(self):
        self.paths = []

    def tearDown(self):
        for path in self.paths:
            path.unlink(missing_ok=True)

    def file(self, name, data):
        path = HERE / ("_test_" + name)
        self.paths.append(path)
        path.write_bytes(data)
        return path

    def test_reject_html_malformed_midi_and_wrong_extension(self):
        self.assertEqual(inspect(self.file("html.mid", b"<!DOCTYPE html><title>bad</title>"))["status"], "invalid")
        self.assertEqual(inspect(self.file("broken.mid", b"MThd\x00\x00\x00\x06\x00"))["status"], "invalid")
        self.assertIn("extension", inspect(self.file("wrong.gp5", midi([(0, b"\xff\x2f\x00")])))["error"])

    def test_midi_requires_explicit_end_of_track_on_every_track(self):
        data = midi([(0, b"\x90\x3c\x60"), (480, b"\x80\x3c\x00")])
        result = inspect(self.file("no_eot.mid", data))
        self.assertEqual(result["status"], "invalid")
        self.assertIn("end_of_track", result["error"])
        first = midi([(0, b"\xff\x2f\x00")])
        second = b"MTrk" + struct.pack(">I", 3) + b"\x00\xc0\x01"
        multi = first[:8] + struct.pack(">HHH", 1, 2, 480) + first[14:] + second
        self.assertIn("end_of_track", inspect(self.file("other_no_eot.mid", multi))["error"])

    def test_missing_midi_tempo_meter_are_assumptions_not_facts(self):
        data = midi([(0, b"\x90\x3c\x60"), (480, b"\x80\x3c\x00"),
                     (0, b"\xff\x2f\x00")])
        result = inspect(self.file("assumed.mid", data))
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["confidence"], "partial")
        self.assertTrue(result["tempos"][0]["assumed"])
        self.assertTrue(result["bars"][0]["meter_assumed"])
        self.assertTrue(result["bars"][0]["duration_seconds_assumed"])
        self.assertTrue(any("tempo not specified" in x for x in result["missing_fields"]))
        self.assertTrue(any("meter not specified" in x for x in result["missing_fields"]))

    def test_no_note_midi_fails_validate_with_partial_metadata(self):
        path = self.file("empty_score.mid", midi([(480, b"\xff\x2f\x00")]))
        result = inspect(path)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["confidence"], "partial")
        self.assertEqual(result["metadata"]["note_count"], 0)
        cli = subprocess.run([sys.executable, str(HERE / "music_assets.py"),
                              "validate", "--path", str(path)], capture_output=True, text=True)
        self.assertNotEqual(cli.returncode, 0)
        rejected = json.loads(cli.stdout)
        self.assertEqual(rejected["status"], "invalid")
        self.assertEqual(rejected["metadata"]["note_count"], 0)
        self.assertEqual(rejected["confidence"], "partial")

    def test_fake_archive_traversal_and_bomb(self):
        self.assertEqual(inspect(self.file("fake.gp", b"PK\x03\x04junk"))["status"], "invalid")
        self.assertIn("unsafe archive path", inspect(self.file(
            "traversal.gp", gp_zip(b"<GPIF/>", "../escape")))["error"])
        bomb = b"<GPIF>" + b" " * 500_000 + b"</GPIF>"
        self.assertIn("unsafe archive member", inspect(self.file(
            "bomb.gp", gp_zip(bomb)))["error"])

    def test_compound_meter_and_tempo_change_within_bar(self):
        data = midi([
            (0, b"\xff\x58\x04\x06\x03\x18\x08"),
            (0, b"\xff\x51\x03\x07\xa1\x20"),  # 120 BPM
            (0, b"\x90\x3c\x60"),
            (480, b"\xff\x51\x03\x0f\x42\x40"),  # 60 BPM after one quarter
            (960, b"\x80\x3c\x00"),
            (0, b"\xff\x2f\x00"),
        ])
        result = inspect(self.file("meter.mid", data))
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["bars"][0]["meter"], "6/8")
        self.assertEqual(result["bars"][0]["length_quarters"], 3)
        self.assertAlmostEqual(result["bars"][0]["duration_seconds"], 2.5)
        self.assertEqual(result["tracks"][0]["notes"][0]["duration_quarters"], 3)

    def test_meter_change_starts_new_bar(self):
        data = midi([(0, b"\xff\x58\x04\x04\x02\x18\x08"),
                     (960, b"\xff\x58\x04\x03\x02\x18\x08"),
                     (1440, b"\xff\x2f\x00")])
        bars = inspect(self.file("meter_change.mid", data))["bars"]
        self.assertEqual([bar["meter"] for bar in bars], ["4/4", "3/4"])
        self.assertEqual([bar["duration_seconds"] for bar in bars], [1, 1.5])

    def test_gpif_parsed_and_tempo_change(self):
        xml = b"""<GPIF>
<Tracks><Track id="0"><Name>Guitar</Name></Track></Tracks>
<MasterTrack><Automations>
<Automation><Type>Tempo</Type><Bar>0</Bar><Position>0</Position><Value>120 2</Value></Automation>
<Automation><Type>Tempo</Type><Bar>1</Bar><Position>0</Position><Value>60 2</Value></Automation>
</Automations></MasterTrack>
<MasterBars><MasterBar><Time>6/8</Time><Bars>0</Bars></MasterBar>
<MasterBar><Time>4/4</Time><Bars>1</Bars></MasterBar></MasterBars>
<Bars><Bar id="0"><Voices>0</Voices></Bar><Bar id="1"><Voices>-1</Voices></Bar></Bars>
<Voices><Voice id="0"><Beats>0</Beats></Voice></Voices>
<Beats><Beat id="0"><Rhythm ref="0"/><Notes>0</Notes></Beat></Beats>
<Rhythms><Rhythm id="0"><NoteValue>Quarter</NoteValue></Rhythm></Rhythms>
<Notes><Note id="0"><Properties><Property name="Midi"><Number>64</Number></Property></Properties></Note></Notes>
</GPIF>"""
        result = inspect(self.file("modern.gp", gp_zip(xml)))
        self.assertEqual(result["status"], "valid")
        self.assertEqual([bar["duration_seconds"] for bar in result["bars"]], [1.5, 4])
        self.assertEqual(result["tracks"][0]["notes"][0]["pitch"], 64)
        self.assertEqual(result["tracks"][0]["tablature"]["status"], "unavailable")
        self.assertIn("note fret", result["tracks"][0]["tablature"]["missing_fields"])
        self.assertTrue(any("accurate tablature unavailable" in field
                            for field in result["missing_fields"]))

    def test_gpx_fixture_parsed_not_just_detected(self):
        result = inspect(ROOT / "daite_tank-volna" / "dayte_tank-volna.gpx")
        self.assertEqual((result["format"], result["status"]), ("gpx", "valid"), result["error"])
        self.assertEqual(result["bar_count"], 123)
        self.assertEqual(result["track_count"], 8)
        self.assertGreater(result["metadata"]["note_count"], 8000)
        self.assertGreater(result["bars"][0]["duration_seconds"], 0)
        self.assertEqual(result["tempos"][0]["bpm"], 150)
        lead = result["tracks"][0]
        self.assertEqual(lead["staves"][0]["tuning_midi"], [40, 45, 50, 55, 59, 64])
        self.assertEqual(lead["staves"][0]["capo_fret"], 0)
        self.assertEqual((lead["notes"][0]["string"], lead["notes"][0]["fret"]), (1, 6))
        self.assertEqual(lead["tablature"]["status"], "complete")
        self.assertNotEqual(result["tracks"][5]["tablature"]["status"], "complete")
        self.assertTrue(any("accurate tablature unavailable" in field
                            for field in result["missing_fields"]))
        self.assertEqual(inspect(self.file("fake.gpx", b"BCFZ\x00\x00\x00\x00"))["status"], "invalid")

    def test_gpif_missing_tempo_is_explicit(self):
        xml = (b"<GPIF><Tracks><Track id='0'/></Tracks><MasterBars>"
               b"<MasterBar><Time>4/4</Time><Bars>0</Bars></MasterBar></MasterBars>"
               b"<Bars><Bar id='0'><Voices>-1</Voices></Bar></Bars></GPIF>")
        result = inspect(self.file("missing_tempo.gp", gp_zip(xml)))
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["confidence"], "partial")
        self.assertIsNone(result["bars"][0]["duration_seconds"])
        self.assertIn("initial tempo", result["missing_fields"])

    def test_modern_gp_fixture_preserves_encoded_tab(self):
        path = ROOT / "daite_tank-djinn" / "daite_tank-djinn.gp"
        result = inspect(path)
        self.assertEqual(result["status"], "valid")
        lead = result["tracks"][0]
        self.assertEqual(lead["staves"][0]["tuning_midi"], [40, 45, 50, 55, 59, 64])
        self.assertEqual(lead["staves"][0]["capo_fret"], 0)
        self.assertEqual(lead["tablature"]["status"], "complete")
        self.assertEqual(lead["tablature"]["string_numbering"], "1=lowest-pitched string")
        note = lead["notes"][0]
        self.assertEqual((note["source_string"], note["string"], note["fret"]), (1, 2, 0))
        self.assertEqual(lead["staves"][0]["tuning_midi"][note["string"] - 1]
                         + note["fret"], note["pitch"])
        self.assertNotEqual(result["tracks"][3]["tablature"]["status"], "complete")

    def test_legacy_gp_preserves_tuning_and_source_string_order(self):
        path = next((ROOT / "white_stripes-seven_nation_army").glob("*.gp3"))
        result = inspect(path)
        self.assertEqual(result["status"], "valid", result["error"])
        track = result["tracks"][0]
        self.assertEqual(track["staves"][0]["capo_fret"], 0)
        self.assertEqual(track["tablature"]["string_numbering"], "1=highest-pitched string")
        self.assertEqual(track["tablature"]["status"], "complete")
        first = track["notes"][0]
        self.assertEqual(track["staves"][0]["tuning_midi"][first["string"] - 1] +
                         first["fret"], first["pitch"])

    def test_gpif_nonzero_capo_and_source_string(self):
        xml = b"""<GPIF>
<MasterTrack><Automations><Automation><Type>Tempo</Type><Bar>0</Bar>
<Position>0</Position><Value>120 2</Value></Automation></Automations></MasterTrack>
<Tracks><Track id="0"><Name>Guitar</Name><Staves><Staff><Properties>
<Property name="Tuning"><Pitches>40 45 50 55 59 64</Pitches></Property>
<Property name="CapoFret"><Fret>2</Fret></Property>
</Properties></Staff></Staves></Track></Tracks>
<MasterBars><MasterBar><Time>4/4</Time><Bars>0</Bars></MasterBar></MasterBars>
<Bars><Bar id="0"><Voices>0</Voices></Bar></Bars>
<Voices><Voice id="0"><Beats>0</Beats></Voice></Voices>
<Beats><Beat id="0"><Rhythm ref="0"/><Notes>0</Notes></Beat></Beats>
<Rhythms><Rhythm id="0"><NoteValue>Quarter</NoteValue></Rhythm></Rhythms>
<Notes><Note id="0"><Properties>
<Property name="Midi"><Number>45</Number></Property>
<Property name="String"><String>0</String></Property>
<Property name="Fret"><Fret>3</Fret></Property>
</Properties></Note></Notes></GPIF>"""
        result = inspect(self.file("capo.gp", gp_zip(xml)))
        self.assertEqual(result["status"], "valid", result["error"])
        track = result["tracks"][0]
        self.assertEqual(track["staves"][0]["capo_fret"], 2)
        self.assertEqual(track["tablature"]["status"], "complete")
        self.assertEqual((track["notes"][0]["source_string"],
                          track["notes"][0]["string"], track["notes"][0]["fret"]),
                         (0, 1, 3))
    def test_read_only_fixtures_and_cli(self):
        fixtures = [("white_stripes-seven_nation_army", ".mid", "midi"),
                    ("white_stripes-seven_nation_army", ".gp3", "gp3"),
                    ("radiohead-karma_police", ".gp4", "gp4"),
                    ("radiohead-creep", ".gp5", "gp5"),
                    ("daite_tank-djinn", ".gp", "gp-zip")]
        for folder, extension, expected in fixtures:
            with self.subTest(extension=extension):
                path = next((ROOT / folder).glob("*" + extension))
                result = inspect(path, include_notes=False)
                self.assertEqual(result["status"], "valid", result["error"])
                self.assertEqual(result["format"], expected)
                self.assertEqual(result["source"], str(path.resolve()))
                self.assertGreater(result["bar_count"], 0)
                self.assertGreater(result["track_count"], 0)
        path = next((ROOT / "white_stripes-seven_nation_army").glob("*.mid"))
        command = [sys.executable, str(HERE / "music_assets.py")]
        output = subprocess.run(command + ["validate", "--path", str(path)],
                                capture_output=True, text=True)
        self.assertEqual(output.returncode, 0, output.stderr)
        validated = json.loads(output.stdout)
        self.assertEqual(validated["status"], "valid")
        self.assertEqual(validated["checksum_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertGreater(validated["metadata"]["note_count"], 0)
        self.assertNotIn("notes", validated["tracks"][0])
        detailed = subprocess.run(command + ["inspect", "--path", str(path)],
                                  capture_output=True, text=True)
        self.assertEqual(detailed.returncode, 0, detailed.stderr)
        self.assertIn("notes", json.loads(detailed.stdout)["tracks"][0])
        duration = subprocess.run(command + ["duration", "--path", str(path)],
                                  capture_output=True, text=True)
        self.assertEqual(duration.returncode, 0, duration.stderr)
        summary = json.loads(duration.stdout)
        self.assertGreater(summary["total_seconds"], 0)
        self.assertEqual(len(summary["bars"]), validated["metadata"]["bar_count"])
        self.assertEqual(summary["repeat_expansion"], "not applied")

    def test_cli_invalid_nonzero_and_json(self):
        path = self.file("bad.mid", b"<html>not music</html>")
        output = subprocess.run([sys.executable, str(HERE / "music_assets.py"),
                                 "validate", "--path", str(path)],
                                capture_output=True, text=True)
        self.assertNotEqual(output.returncode, 0)
        result = json.loads(output.stdout)
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["checksum_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_exact_fractional_integration(self):
        from fractions import Fraction
        self.assertAlmostEqual(bar_seconds(Fraction(0), Fraction(3),
                                            [(0, 120), (Fraction(3, 2), 60)]), 2.25)


if __name__ == "__main__":
    unittest.main()
