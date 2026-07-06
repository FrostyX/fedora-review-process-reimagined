import logging
import subprocess
import tempfile
import requests
from pathlib import Path
from copr.v3 import Client
from ogr.services.forgejo import ForgejoService


COPR_OWNER = "@fedora-review"
FORGEJO_INSTANCE = "http://forgejo:3000"
FORGEJO_NAMESPACE = "packaging"
FORGEJO_REPO = "package-review"
PACKAGE_REVIEW_REPO = f"{FORGEJO_INSTANCE}/{FORGEJO_NAMESPACE}/{FORGEJO_REPO}"
FORGEJO_TOKEN = "10ff559b9e1dc5c11992602090e9e29dbe164185"


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
    builds = client.build_proxy.get_list(owner, project)
    for build in builds:
        if build.ended_on:
            continue
        client.build_proxy.cancel(build.id)
    client.build_proxy.delete_list([x.id for x in builds])


def main():
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger(__name__)

    # TODO Read these from outside (argparser, or more likely ENV variables)
    force = False
    pull_request = 1
    projectname = f"fedora-review-pr-{pull_request}"
    chroots = ["fedora-rawhide-x86_64", "fedora-44-x86_64"]

    # TODO This will get all the changes within that PR. Therefore if somebody
    # adds only one commit with one package to an existing PR of many packages,
    # we will get all of them and rebuild all of the packages.
    changes = forgejo_changed_files_per_commit(
        FORGEJO_NAMESPACE,
        FORGEJO_REPO,
        pull_request,
    )

    with tempfile.TemporaryDirectory() as tmp:
        copr = Client.create_from_config_file()
        if force:
            copr_wipe_project(copr, COPR_OWNER, projectname)
        copr.project_proxy.add(COPR_OWNER, projectname, chroots, exist_ok=True)

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
