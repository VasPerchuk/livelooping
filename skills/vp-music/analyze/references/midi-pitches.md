# MIDI pitch and GM percussion quick reference

MIDI note numbers are 0–127. For **pitched** notes, pitch class is the number
modulo 12: `0 C, 1 C#, 2 D, 3 D#, 4 E, 5 F, 6 F#, 7 G, 8 G#, 9 A,
10 A#, 11 B`. Use flats where the harmony calls for them. This reference uses
**C4 = MIDI 60** (middle C): octave = `floor(number / 12) - 1`.
Examples: 0 = C-1, 21 = A0, 36 = C2, 48 = C3, 60 = C4, 69 = A4,
72 = C5, 127 = G9. Some DAWs label these same numbers one octave higher or
lower; the MIDI number is unambiguous.

In **General MIDI (GM)**, channel 10 is percussion (the parser reports
zero-based `channel: 9`). The pitch number then selects a *drum sound*, not
a musical note or octave. Standard GM percussion keys are 35–81:

| Key | GM sound | Key | GM sound |
| ---: | --- | ---: | --- |
| 35 | Acoustic Bass Drum | 59 | Ride Cymbal 2 |
| 36 | Bass Drum 1 | 60 | Hi Bongo |
| 37 | Side Stick | 61 | Low Bongo |
| 38 | Acoustic Snare | 62 | Mute Hi Conga |
| 39 | Hand Clap | 63 | Open Hi Conga |
| 40 | Electric Snare | 64 | Low Conga |
| 41 | Low Floor Tom | 65 | High Timbale |
| 42 | Closed Hi-Hat | 66 | Low Timbale |
| 43 | High Floor Tom | 67 | High Agogo |
| 44 | Pedal Hi-Hat | 68 | Low Agogo |
| 45 | Low Tom | 69 | Cabasa |
| 46 | Open Hi-Hat | 70 | Maracas |
| 47 | Low-Mid Tom | 71 | Short Whistle |
| 48 | Hi-Mid Tom | 72 | Long Whistle |
| 49 | Crash Cymbal 1 | 73 | Short Guiro |
| 50 | High Tom | 74 | Long Guiro |
| 51 | Ride Cymbal 1 | 75 | Claves |
| 52 | Chinese Cymbal | 76 | Hi Wood Block |
| 53 | Ride Bell | 77 | Low Wood Block |
| 54 | Tambourine | 78 | Mute Cuica |
| 55 | Splash Cymbal | 79 | Open Cuica |
| 56 | Cowbell | 80 | Mute Triangle |
| 57 | Crash Cymbal 2 | 81 | Open Triangle |
| 58 | Vibraslap |  |  |

This is a **conditional mapping**, not proof of the rendered sound: a
non-GM kit, remapped pad, or a nonstandard channel can differ. For keys outside
35–81 or an unverified drum map, keep numeric pitch labels rather than naming
kit pieces. Sources: [GM percussion key map](https://www.cs.cmu.edu/~music/cmp/archives/cmsip/readings/GMSpecs_PercMap.htm);
[GM drum names](https://qsrdrums.com/webhelp-responsive/References/r_general_midi_drum_kit.html).
