"""Operator selection for the common Recar single-signal fault-injection runner."""
import argparse

from recar.catalog import choices, select_case

FAMILY_MENU = ('SENT', 'MCU', 'MCU_OS', 'MAGCHIP', 'MULTI_SIGNAL_FIXED', 'SPECIAL_SEQUENCE')


def print_cases(family):
    for case in choices(family):
        detail = f'{len(case.writes)} ordered writes' if case.is_multi_signal else f'value {case.injection_value}'
        print(f'{case.selection_id:3} - {case.expected_fault} ({detail}, Excel {case.excel_row}, {case.hardware_validation_status})')


def choose_family():
    for index, family in enumerate(FAMILY_MENU, 1):
        print(f'{index}. {family}')
    return FAMILY_MENU[int(input('Select family (Ctrl+C to cancel): ')) - 1]


def main():
    parser = argparse.ArgumentParser(description='Recar common single-signal fault-injection runner')
    parser.add_argument('--list', action='store_true', help='List available family selections without hardware access')
    parser.add_argument('--family', choices=FAMILY_MENU)
    parser.add_argument('--case', type=int)
    parser.add_argument('--dry-run', action='store_true', help='Create an offline metadata report; default mode')
    parser.add_argument('--resolve-only', action='store_true', help='Resolve selected case through A2L without ECU connection')
    parser.add_argument('--execute-hardware', action='store_true', help='Run an actual ECU test after hardware is available')
    args = parser.parse_args()
    if args.execute_hardware and (args.dry_run or args.resolve_only):
        parser.error('--execute-hardware cannot be combined with offline modes')
    if args.list:
        if args.family:
            print_cases(args.family)
        else:
            for family in FAMILY_MENU:
                print(f'{family}: {len(choices(family))} cases')
        return
    family = args.family
    if family is None:
        try:
            family = choose_family()
        except (ValueError, EOFError, IndexError):
            parser.error('Unknown family. No hardware operation was performed.')
    if args.case is None:
        print_cases(family)
        try:
            selection = int(input('Select test case (Ctrl+C to cancel): '))
        except (ValueError, EOFError):
            parser.error('Unknown test selection. No hardware operation was performed.')
    else:
        selection = args.case
    try:
        case = select_case(selection, family)
    except ValueError as exc:
        parser.error(str(exc))
    print(f'Selected {case.family} Excel {case.excel_row}: {case.expected_fault}', flush=True)
    if args.execute_hardware:
        from recar.probe import main as execute
        execute(['--case', str(case.selection_id), '--connect', '--inject'])
    elif args.resolve_only:
        from recar.probe import main as execute
        execute(['--case', str(case.selection_id)])
    else:
        from recar.offline import run
        print(run(case))


if __name__ == '__main__':
    main()
