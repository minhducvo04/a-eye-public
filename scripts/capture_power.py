"""20 placement trials. Fake mode tests counting only, never claims battery savings.

Hardware mode prompts for observed events and USB watts; sensors are read locally.
"""
import argparse
import json
import re
import shutil
import subprocess
import time


def sensors():
    out = {}
    for key, command, pattern in (
        ('temperature_c', ['vcgencmd', 'measure_temp'], r'([\d.]+)'),
        ('battery_percent', ['pmset', '-g', 'batt'], r'(\d+)%'),
    ):
        value = None
        if shutil.which(command[0]):
            result = subprocess.run(command, capture_output=True, text=True, timeout=3)
            match = re.search(pattern, result.stdout)
            if match:
                value = float(match[1])
        out[key] = value if value is not None else 'SKIP'
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fake', action='store_true')
    args = parser.parse_args()
    events = misses = 0
    for trial in range(1, 21):
        if args.fake:
            # Drive the production motion rule with a synthetic placement.
            from PIL import Image, ImageDraw
            from device.trigger import Motion
            motion = Motion()
            before = Image.new('L', (64, 48))
            after = before.copy()
            ImageDraw.Draw(after).rectangle((20, 20, 40, 40), fill=255)
            motion.changed(before)
            hit = motion.changed(after)
            watts = 'SKIP'
        else:
            hit = input(f'Trial {trial}: place object; Event observed? [y/N] ').lower() == 'y'
            reading = input('USB meter watts (Enter if unavailable): ').strip()
            watts = float(reading) if reading else 'SKIP'
        events += int(hit)
        misses += int(not hit)
        print(json.dumps(dict(t=time.time(), trial=trial, state='Event' if hit else 'Armed',
                              events=events, miss_count=misses, usb_watts=watts,
                              mode='fake' if args.fake else 'observed', **sensors())), flush=True)
    print(f'SUMMARY trials=20 events={events} misses={misses}; '
          'SKIP physical capture/power proof' if args.fake else
          f'SUMMARY trials=20 events={events} misses={misses}')


if __name__ == '__main__':
    main()
