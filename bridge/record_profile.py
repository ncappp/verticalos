#!/usr/bin/env python3
"""Record exact UI selectors from a user's own device. Run while bridge is stopped."""

import argparse, json, re
from pathlib import Path
from adb_control import ADB, PACKAGES, parse_nodes, ScreenError


def select(adb, label, editable=False):
    raw = adb.tree()
    nodes = parse_nodes(raw)
    visible = [
        n
        for n in nodes
        if (n.get('text') or n.get('content-desc') or n.get('resource-id'))
        and (not editable or n.get('class') == 'android.widget.EditText')
    ]
    if not visible:
        raise ScreenError(
            'This screen does not expose suitable UI nodes. Stop and provide screenshot/ui.xml for calibration.'
        )
    print('\n' + label)
    for i, n in enumerate(visible):
        print(i, n.get('text'), n.get('content-desc'), n.get('resource-id'), n['bounds'])
    chosen = visible[int(input('Node number: '))]
    selector = {}
    if chosen.get('resource-id'):
        selector['id'] = chosen['resource-id']
    if chosen.get('text'):
        selector['text'] = chosen['text']
    elif chosen.get('content-desc'):
        selector['desc'] = chosen['content-desc']
    if not selector:
        raise ScreenError('No stable selector; stop rather than use fixed coordinates')
    if (
        len(
            [
                n
                for n in nodes
                if all(
                    n.get({'id': 'resource-id', 'text': 'text', 'desc': 'content-desc'}[k]) == v
                    for k, v in selector.items()
                )
            ]
        )
        != 1
    ):
        raise ScreenError('Selected element is ambiguous')
    return selector


def record_section(adb, title, allow_publish=False):
    result = []
    print(
        '\n'
        + title
        + ' — navigate manually on the phone between captures; no publish tap is sent by this recorder.'
    )
    while True:
        choice = input(
            '[t] tap, [c] caption input, [b] back, [a] assert, [p] publish selector, [d] done: '
        ).strip()
        if choice == 'd':
            return result
        if choice == 'b':
            result.append({'action': 'back'})
            continue
        if choice == 'c':
            selector = select(adb, 'Select empty caption editor', True)
            value = input('Variable [caption/title]: ').strip()
            if value not in ('caption', 'title'):
                raise ScreenError('Invalid variable')
            result.append({'action': 'text', 'selector': selector, 'value': value})
            continue
        if choice in ('t', 'a', 'p'):
            selector = select(adb, 'Select exact element')
            if choice == 'p' and not allow_publish:
                raise ScreenError('Publish selector allowed only in compose section')
            step = {'action': 'assert' if choice == 'a' else 'tap', 'selector': selector}
            if choice == 'p':
                step['publish'] = True
                result.append(step)
                return result
            if choice == 'a':
                variable = input('Replace exact text with variable? [caption/title/empty]: ').strip()
                if variable:
                    if variable not in ('caption', 'title'):
                        raise ScreenError('Invalid variable')
                    selector['text'] = '${' + variable + '}'
            result.append(step)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--serial', required=True)
    parser.add_argument('--platform', choices=list(PACKAGES), required=True)
    parser.add_argument('--package')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    adb = ADB(args.serial)
    adb.ready()
    package = args.package or PACKAGES[args.platform]
    version = re.search(r'versionName=([^\s]+)', adb.shell('dumpsys', 'package', package))
    if not version:
        raise ScreenError('App not installed')
    profile = {
        'enabled': False,
        'platform': args.platform,
        'package': package,
        'app_version': version.group(1),
    }
    if args.platform == 'VK':
        profile['publication_type'] = 'VK_CLIPS'
    print('Open app landing screen. Record navigation to the account profile:')
    profile['preflight'] = record_section(adb, 'PRE-FLIGHT')
    account = select(adb, 'Select the visible username on the actual account profile')
    if 'text' in account:
        account['text'] = '${username}'
    elif 'desc' in account:
        account['desc'] = '${username}'
    else:
        raise ScreenError('Account username must be visible as exact text or description')
    profile['account_selector'] = account
    input(
        'Manually share a test MP4 into the target application and choose the correct mode (Shorts/Reels/Clips). Press Enter: '
    )
    profile['compose'] = record_section(adb, 'COMPOSE — record navigation and final publish selector', True)
    input(
        'For verification, manually publish your permitted TEST video and navigate to a screen showing its exact caption/title. Press Enter: '
    )
    profile['verification'] = record_section(adb, 'VERIFICATION — include assert of the exact caption/title')
    file = Path(args.out)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding='utf8')
    print(
        'Saved DISABLED profile. Validate the recorded sequence; set enabled=true only after a successful supervised test. No untested universal profile is supplied.'
    )


if __name__ == '__main__':
    main()
