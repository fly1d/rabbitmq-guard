#!/usr/bin/env python3
import argparse
import email.parser
import re
import tarfile
import zipfile
from pathlib import Path


SCENARIO_SUFFIXES = {
    "scenarios/00_healthy_baseline.json",
    "scenarios/01_no_consumers.json",
    "scenarios/02_growing_backlog.json",
    "scenarios/03_unacked_saturation.json",
    "scenarios/04_redelivery_loop.json",
    "scenarios/05_memory_alarm.json",
    "scenarios/06_disk_alarm.json",
    "scenarios/07_fd_pressure.json",
    "scenarios/08_quorum_degraded.json",
    "scenarios/09_quorum_lost.json",
    "scenarios/10_connection_churn.json",
}
WEB_SUFFIXES = {"web/app.js", "web/index.html", "web/styles.css"}


def require_suffixes(names, suffixes, label):
    for suffix in sorted(suffixes):
        if not any(name.endswith("rabbitmq_guard/" + suffix) for name in names):
            raise SystemExit("{} is missing {}".format(label, suffix))


def wheel_component(value):
    return re.sub(r"[^\w\d.]+", "_", value, flags=re.UNICODE)


def read_metadata(raw, label):
    metadata = email.parser.BytesParser().parsebytes(raw)
    for field in ("Name", "Version", "License-Expression", "Requires-Python"):
        if not metadata[field]:
            raise SystemExit("{} metadata is missing {}".format(label, field))
    return metadata


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument("sdist", type=Path)
    parser.add_argument("--expected-version")
    args = parser.parse_args()
    wheel = args.wheel
    sdist = args.sdist
    with zipfile.ZipFile(wheel, "r") as archive:
        wheel_names = set(archive.namelist())
        metadata_name = next(
            name for name in wheel_names if name.endswith(".dist-info/METADATA")
        )
        wheel_metadata = read_metadata(archive.read(metadata_name), "wheel")
    with tarfile.open(sdist, "r:gz") as archive:
        sdist_names = set(archive.getnames())
        pkg_info_name = next(
            name
            for name in sdist_names
            if name.count("/") == 1 and name.endswith("/PKG-INFO")
        )
        pkg_info = archive.extractfile(pkg_info_name)
        if pkg_info is None:
            raise SystemExit("sdist PKG-INFO cannot be read")
        sdist_metadata = read_metadata(pkg_info.read(), "sdist")

    for names, label in ((wheel_names, "wheel"), (sdist_names, "sdist")):
        require_suffixes(names, SCENARIO_SUFFIXES, label)
        require_suffixes(names, WEB_SUFFIXES, label)
    if not any(name.endswith(".dist-info/licenses/LICENSE") for name in wheel_names):
        raise SystemExit("wheel is missing LICENSE")
    if not any(name.endswith("/LICENSE") for name in sdist_names):
        raise SystemExit("sdist is missing LICENSE")
    for field in ("Name", "Version", "License-Expression", "Requires-Python"):
        if wheel_metadata[field] != sdist_metadata[field]:
            raise SystemExit("wheel and sdist disagree on {}".format(field))

    name = wheel_component(wheel_metadata["Name"])
    version = wheel_component(wheel_metadata["Version"])
    if not wheel.name.startswith("{}-{}-".format(name, version)):
        raise SystemExit("wheel filename does not match package metadata")
    if sdist.name != "{}-{}.tar.gz".format(name, version):
        raise SystemExit("sdist filename does not match package metadata")
    if args.expected_version and wheel_metadata["Version"] != args.expected_version:
        raise SystemExit(
            "package version {} does not match expected version {}".format(
                wheel_metadata["Version"], args.expected_version
            )
        )
    if wheel_metadata["License-Expression"] != "Apache-2.0":
        raise SystemExit("unexpected license expression")
    if wheel_metadata["Requires-Python"] != ">=3.9":
        raise SystemExit("unexpected Python requirement")
    print("distribution contents passed")


if __name__ == "__main__":
    main()
