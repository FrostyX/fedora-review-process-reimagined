import argparse
import logging
import subprocess
import tempfile
import requests
from pathlib import Path
from contextlib import suppress
from copr.v3 import Client, CoprRequestException, CoprNoResultException
from ogr.services.forgejo import ForgejoService


COPR_OWNER = "@fedora-review"
FORGEJO_INSTANCE = "http://forgejo:3000"
FORGEJO_NAMESPACE = "packaging"
FORGEJO_REPO = "package-review"
PACKAGE_REVIEW_REPO = f"{FORGEJO_INSTANCE}/{FORGEJO_NAMESPACE}/{FORGEJO_REPO}"
FORGEJO_TOKEN = "10ff559b9e1dc5c11992602090e9e29dbe164185"


def get_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pull-request",
        type=int,
        required=True,
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
    )
    return parser


def forgejo_changed_files_per_commit(
    namespace: str,
    repo: str,
    pull_request: int,
) -> list[dict]:
    service = ForgejoService(instance_url=FORGEJO_INSTANCE, token=FORGEJO_TOKEN)
    project = service.get_project(repo=repo, namespace=namespace)
    pr = project.get_pr(pull_request)

    result = []
    for sha in pr.get_all_commits():
        commit = service.api.repository.repo_get_single_commit(
            owner=namespace,
            repo=repo,
            sha=sha,
        )
        result.append({
            "commit": sha,
            "files": [x.filename for x in commit.files],
        })
    return result


def forgejo_file_url(namespace: str, repo: str, commit: str, filename: str):
    return f"{FORGEJO_INSTANCE}/{namespace}/{repo}/raw/commit/{commit}/{filename}"


def copr_wipe_project(client: Client, owner: str, project: str) -> None:
    """
    It would be nice to just delete the whole project but we are facing
    a race condition in Copr - https://github.com/fedora-copr/copr/issues/4184
    Therefore, we need to cancel and delete all the builds while preserving the
    project itself.
    """
    with suppress(CoprNoResultException):
        builds = client.build_proxy.get_list(owner, project)
        for build in builds:
            if build.ended_on:
                continue
            client.build_proxy.cancel(build.id)
        client.build_proxy.delete_list([x.id for x in builds])


def fedora_chroots_available_in_copr(client: Client):
    chroots = client.mock_chroot_proxy.get_list()
    return [
        x for x in chroots.keys()
        if x.startswith("fedora-")
        and x.endswith("-x86_64")
        and not x.startswith("fedora-eln")
    ]


def copr_create_or_update_project(
    client: Client,
    owner: str,
    project: str,
    chroots: list[str],
) -> None:
    try:
        client.project_proxy.add(
            owner,
            project,
            chroots=chroots,
            unlisted_on_hp=True,
        )
    except CoprRequestException as ex:
        if "already has a project named" not in str(ex):
            raise CoprRequestException(str(ex)) from ex
        client.project_proxy.edit(owner, project, chroots)


def main():
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger(__name__)

    parser = get_arg_parser()
    args = parser.parse_args()
    projectname = f"fedora-review-pr-{args.pull_request}"

    # TODO This will get all the changes within that PR. Therefore if somebody
    # adds only one commit with one package to an existing PR of many packages,
    # we will get all of them and rebuild all of the packages.
    changes = forgejo_changed_files_per_commit(
        FORGEJO_NAMESPACE,
        FORGEJO_REPO,
        args.pull_request,
    )

    with tempfile.TemporaryDirectory() as tmp:
        copr = Client.create_from_config_file()
        if args.force:
            copr_wipe_project(copr, COPR_OWNER, projectname)

        chroots = fedora_chroots_available_in_copr(copr)
        copr_create_or_update_project(copr, COPR_OWNER, projectname, chroots)

        previous_build_id = None
        for change in changes:
            for filename in change["files"]:
                # We may not actually want to use the change["commit"] but
                # rather find out what was the latest commit that changed that
                # exact file and submit the build from that commit/file.
                url = forgejo_file_url(
                    FORGEJO_NAMESPACE,
                    FORGEJO_REPO,
                    change["commit"],
                    filename,
                )
                response = requests.get(url)
                response.raise_for_status()
                spec = Path(tmp) / filename
                spec.write_bytes(response.content)

            # TODO If we have two spec files added within one commit, we should
            # be able to build them in paralel, therefore `with_build_id`
            # instead of `after_build_id`.
            buildopts = {}
            if previous_build_id:
                buildopts = {"after_build_id": previous_build_id}

            build = copr.build_proxy.create_from_file(
                COPR_OWNER,
                projectname,
                spec,
                buildopts=buildopts,
            )
            log.info("Copr build: %s", build.id)
            previous_build_id = build.id


if __name__ == "__main__":
    main()
