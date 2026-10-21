#!/usr/bin/env python3
"""Extract exact decoded frames for motion inspection using Python + FFmpeg."""
import argparse
import json
import math
from fractions import Fraction
from pathlib import Path
import shutil
import subprocess
import sys


def run(command):
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('video', type=Path)
    parser.add_argument('--start-frame', type=int, required=True)
    parser.add_argument('--end-frame', type=int, required=True,
                        help='Exclusive end; ranges are [start, end).')
    parser.add_argument('--step', type=int, default=1,
                        help='1 extracts every frame; larger values are sparse samples.')
    parser.add_argument('--cols', type=int, default=5)
    parser.add_argument('--thumb-width', type=int, default=480)
    parser.add_argument('--output', type=Path, required=True,
                        help='A new directory. Existing directories are never overwritten.')
    args = parser.parse_args()
    if not args.video.is_file():
        parser.error('Video does not exist.')
    if args.start_frame < 0 or args.end_frame <= args.start_frame:
        parser.error('Expected 0 <= start-frame < end-frame.')
    if args.step < 1 or args.cols < 1 or args.thumb_width < 32:
        parser.error('step/cols must be positive and thumb-width >= 32.')
    expected = math.ceil((args.end_frame - args.start_frame) / args.step)
    if expected > 180:
        parser.error('At most 180 frames per sheet; split a long interval into batches.')
    for binary in ('ffprobe', 'ffmpeg'):
        if not shutil.which(binary):
            parser.error(f'{binary} is required on PATH.')
    if args.output.exists():
        parser.error('Output directory already exists; choose a new directory.')

    video = args.video.resolve()
    probe = json.loads(run([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height,r_frame_rate,avg_frame_rate,nb_frames',
        '-of', 'json', str(video),
    ]))
    if not probe.get('streams'):
        parser.error('No video stream found.')
    stream = probe['streams'][0]
    average = Fraction(stream.get('avg_frame_rate', '0/1'))
    nominal = Fraction(stream.get('r_frame_rate', '0/1'))
    if average <= 0 or average != nominal:
        parser.error('Variable or unknown frame rate: normalize a separate copy to CFR first.')
    total = stream.get('nb_frames')
    if total and total.isdigit() and args.end_frame > int(total):
        parser.error(f'end-frame exceeds the {total} decoded frames declared by this file.')

    output = args.output.resolve()
    output.mkdir(parents=True)
    selector = (f'select=gte(n\\,{args.start_frame})*lt(n\\,{args.end_frame})'
                f'*not(mod(n-{args.start_frame}\\,{args.step}))')
    # No input seek: n is the decoded frame index from the start of the file.
    run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', str(video),
         '-map', '0:v:0', '-an', '-vf', selector, '-fps_mode', 'vfr',
         '-frames:v', str(expected), str(output / 'frame-%04d.png')])
    frames = sorted(output.glob('frame-*.png'))
    if len(frames) != expected:
        raise RuntimeError(f'Expected {expected} frames, extracted {len(frames)}; '
                           f'partial output kept at {output}.')
    columns = min(args.cols, expected)
    rows = math.ceil(expected / columns)
    run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-framerate', '1',
         '-i', str(output / 'frame-%04d.png'),
         '-vf', f'scale={args.thumb_width}:-2,tile={columns}x{rows}:padding=4:margin=4',
         '-frames:v', '1', str(output / 'contact-sheet.jpg')])
    records = [
        {'file': frame.name, 'source_frame': args.start_frame + i * args.step,
         'seconds_from_first_frame': float(Fraction(args.start_frame + i * args.step) / average),
         'cell': {'row': i // columns + 1, 'column': i % columns + 1}}
        for i, frame in enumerate(frames)
    ]
    metadata = {
        'video': str(video), 'fps': str(average),
        'range': [args.start_frame, args.end_frame], 'range_end': 'exclusive',
        'step': args.step, 'extracted_frames': len(frames),
        'source_dimensions': [stream['width'], stream['height']],
        'coverage': 'every-frame' if args.step == 1 else 'sparse-samples',
        'review_status': 'unreviewed', 'audio_status': 'not-reviewed',
        'scope': 'Extraction evidence only; human visual review and playback are separate.',
        'frames': records,
    }
    (output / 'frames.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'output': str(output), 'frames': len(frames),
                      'coverage': metadata['coverage'], 'review_status': 'unreviewed'},
                     ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (subprocess.CalledProcessError, ValueError, RuntimeError) as error:
        print(getattr(error, 'stderr', None) or str(error), file=sys.stderr)
        sys.exit(1)
