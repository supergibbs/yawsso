import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

from yawsso import PROGRAM, TRACE, Constant, logger, core, utils, cmd


def _boot():
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(message)s')  # print UNIX friendly format for PIPE use case
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    parser = argparse.ArgumentParser(
        prog=PROGRAM, description="Sync all named profiles when calling without any arguments"
    )
    parser.add_argument("--default", action="store_true", help="Sync AWS default profile and all named profiles")
    parser.add_argument("--default-only", action="store_true", help="Sync AWS default profile only and exit")
    parser.add_argument("-p", "--profiles", nargs="*", metavar="", help="Sync specified AWS named profiles")
    parser.add_argument("-b", "--bin", metavar="", help="AWS CLI v2 binary location (default to `aws` in PATH)")
    parser.add_argument("-d", "--debug", help="Debug output", action="store_true")
    parser.add_argument("-t", "--trace", help="Trace output", action="store_true")
    parser.add_argument("-e", "--export-vars", dest="export_vars1", help="Print out AWS ENV vars", action="store_true")
    parser.add_argument("-v", "--version", help="Print version and exit", action="store_true")

    sp = parser.add_subparsers(title="available commands", metavar="", dest="command")
    login_help = "Invoke aws sso login and sync all named profiles"
    login_description = f"{login_help}\nUse `default` profile or `AWS_PROFILE` if optional argument `--profile` absent"
    login_command = sp.add_parser(
        "login", description=login_description, help=login_help, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    login_command.add_argument("-e", "--export-vars", help="Print out AWS ENV vars", action="store_true")
    login_command.add_argument("--profile", help="Login profile (use `default` or `AWS_PROFILE` if absent)", metavar="")
    login_command.add_argument("--this", action="store_true", help="Only sync this login profile")
    sp.add_parser("encrypt", help=f"Encrypt ({Constant.ROT_13.value.upper()}) stdin and exit")
    sp.add_parser("decrypt", help=f"Decrypt ({Constant.ROT_13.value.upper()}) stdin and exit")
    sp.add_parser("version", help="Print version and exit")

    args = parser.parse_args()

    if args.version:
        logger.info(Constant.VERSION_HELP.value)
        exit(0)  # just version print, don't even bother all the rest

    if args.trace:
        formatter = logging.Formatter('%(asctime)s %(name)-12s %(levelname)-8s %(message)s')
        handler.setFormatter(formatter)
        logger.setLevel(TRACE)
        logger.log(TRACE, "Logging level: TRACE")

    if args.debug:
        formatter = logging.Formatter('%(asctime)s %(name)-12s %(levelname)-8s %(message)s')
        handler.setFormatter(formatter)
        logger.setLevel(logging.DEBUG)
        logger.debug("Logging level: DEBUG")

    if args.bin:
        core.aws_bin = args.bin

    logger.log(TRACE, f"args: {args}")
    logger.log(TRACE, f"AWS_CONFIG_FILE: {core.aws_config_file}")
    logger.log(TRACE, f"AWS_SHARED_CREDENTIALS_FILE: {core.aws_shared_credentials_file}")
    logger.log(TRACE, f"AWS_SSO_CACHE_PATH: {core.aws_sso_cache_path}")
    logger.log(TRACE, f"Cache SSO JSON files: {utils.list_directory(core.aws_sso_cache_path)}")

    _preflight()

    return cmd.Command(args=args)


def _preflight():
    if not os.path.exists(core.aws_shared_credentials_file):
        logger.debug(f"{core.aws_shared_credentials_file} file does not exist. Attempting to create one.")
        try:
            Path(os.path.dirname(core.aws_shared_credentials_file)).mkdir(parents=True, exist_ok=True)
            with open(core.aws_shared_credentials_file, "w"):
                pass
        except Exception as e:
            logger.debug(f"Can not create {core.aws_shared_credentials_file}. Exception: {e}")
            utils.halt(f"{core.aws_shared_credentials_file} file does not exist. Please create one and try again.")

    if not os.path.exists(core.aws_config_file):
        utils.halt(f"{core.aws_config_file} does not exist")

    if not os.path.exists(core.aws_sso_cache_path):
        utils.halt(f"{core.aws_sso_cache_path} does not exist")

    if shutil.which(core.aws_bin) is None:
        utils.halt(f"Can not find AWS CLI v2 `{core.aws_bin}` command.")

    cmd_aws_cli_version = f"{core.aws_bin} --version"
    aws_cli_success, aws_cli_version_output = utils.invoke(cmd_aws_cli_version)

    if not aws_cli_success:
        utils.halt(f"ERROR EXECUTING COMMAND: '{cmd_aws_cli_version}'. EXCEPTION: {aws_cli_version_output}")

    if "aws-cli/2" not in aws_cli_version_output:
        utils.halt(f"Required AWS CLI v2. Found {aws_cli_version_output}")

    logger.debug(aws_cli_version_output)


def main():
    co = _boot()

    if co.args.command:
        co.dispatch()  # subcommand dispatch

    # then continue with sync all named profiles below if subcommand does not happen to exit the program yet

    # Specific use case: making `yawsso -e` behaviour to sync default profile, print cred then exit
    if co.export_vars and not co.args.default and not co.args.profiles and not hasattr(co.args, 'profile'):
        credentials = core.update_profile("default", co.config)
        utils.get_export_vars("default", credentials)
        exit(0)

    # Specific use case: two flags to take care of default profile sync behaviour
    if co.args.default or co.args.default_only:
        credentials = core.update_profile("default", co.config)
        if co.export_vars:
            utils.get_export_vars("default", credentials)
        if co.args.default_only:
            exit(0)

    # Main use case: sync all named profiles
    n_profiles = list(map(lambda p: p.replace("profile ", ""), filter(lambda s: s != "default", co.config.sections())))
    logger.debug(f"Current named profiles in config: {str(n_profiles)}")

    core.profiles = n_profiles

    if co.args.profiles:
        profiles = []
        for np in co.args.profiles:
            if ":" in np:
                old, new = np.split(":")
                if old not in n_profiles:
                    logger.warning(f"Named profile `{old}` is not specified in {core.aws_config_file}. Skipping...")
                    continue
                logger.debug(f"Renaming profile {old} to {new}")
                profiles.append(old)
                co.profiles_new_name[old] = new
            elif np.endswith("*"):
                prefix = np.split("*")[0]
                logger.log(TRACE, f"Collecting all named profiles start with '{prefix}'")
                profiles.extend(list(filter(lambda _p: _p.startswith(prefix), n_profiles)))
            else:
                if np not in n_profiles:
                    logger.warning(f"Named profile `{np}` is not specified in {core.aws_config_file}. Skipping...")
                    continue
                profiles.append(np)
        # end for
        core.profiles = list(set(profiles))  # dedup

    logger.debug(f"Syncing named profiles: {str(core.profiles)}")
    for profile_name in core.profiles:
        if profile_name in co.profiles_new_name:
            credentials = core.update_profile(profile_name, co.config, co.profiles_new_name[profile_name])
        else:
            credentials = core.update_profile(profile_name, co.config)

        # a bit awkward but if user wish to
        if co.export_vars:
            utils.get_export_vars(profile_name, credentials)
