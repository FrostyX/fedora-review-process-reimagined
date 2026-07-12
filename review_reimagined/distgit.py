"""
Inspired by Copr DistGit importer
https://github.com/fedora-copr/copr/blob/main/dist-git/copr_dist_git/package_import.py"""

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from pyrpkg import Commands
from pyrpkg.errors import rpkgError
from functools import partial


SETUP_GIT_PACKAGE = "/usr/share/dist-git/setup_git_package"
MKBRANCH = "/usr/share/dist-git/mkbranch"
GIT_BASE_URL = "/var/lib/dist-git/git/%(module)s"
LOOKASIDE_LOCATION = "/var/lib/dist-git/cache/lookaside/pkgs/"
NAMESPACE = "rpms/"


class PackageImportException(Exception):
    strtype = 'unknown_error'


@dataclass(frozen=True)
class GitUser:
    name: str
    email: str


class DistGit:
    def __init__(self, log):
        self.log = log

    def create_repository(
        self,
        reponame: str,
        branches: list[str],
        exist_ok: bool = True,
    ) -> None:
        """
        Initialize a new DistGit repository
        """
        self.log.info("make sure repos exist: {}".format(reponame))
        brand_new_package = False
        try:
            cmd = [SETUP_GIT_PACKAGE, reponame]
            subprocess.check_output(cmd, stderr=subprocess.STDOUT, encoding='utf-8')
            brand_new_package = True
        except subprocess.CalledProcessError as e:
            self.log.error("cmd: {}, rc: {}, msg: {}"
                      .format(cmd, e.returncode, e.output.strip()))
            if exist_ok and e.returncode == 128:
                self.log.info("Package already exists...continuing")
            else:
                raise PackageImportException(e.output)

        for branch in branches:
            try:
                cmd = [MKBRANCH, branch, reponame]
                subprocess.check_output(cmd, stderr=subprocess.STDOUT, encoding='utf-8')
            except subprocess.CalledProcessError as e:
                self.log.error("cmd: {}, rc: {}, msg: {}"
                          .format(cmd, e.returncode, e.output.strip()))
                if exist_ok and e.returncode == 128:
                    self.log.info("Branch already exists...continuing")
                else:
                    raise PackageImportException(e.output)

    def update_description(self, reponame: str, description: str) -> None:
        path = f"/var/lib/dist-git/git/rpms/{reponame}.git/description"
        with open(path, "w") as fp:
            print(description, file=fp)

    def run_git_config(self, name: str, email: str) -> None:
        subprocess.run(["git", "config", "user.name", name], check=True)
        subprocess.run(["git", "config", "user.email", email], check=True)

    def set_default_branch(self, reponame: str, branch: str) -> None:
        path = f"/var/lib/dist-git/git/rpms/{reponame}.git"
        cmd = ["git", "symbolic-ref", "HEAD", f"refs/heads/{branch}"]
        subprocess.run(cmd, check=True, cwd=path)

    def rebase(self, committish: str) -> None:
        cmd = ["git", "rebase", committish]
        subprocess.run(cmd, check=True)

    def import_package(
        self,
        reponame: str,
        branches: str,
        srpm_path: str,
        user: GitUser,
    ) -> str:
        """
        Import package into a DistGit repo for the given branches.
        """
        repo_dir = tempfile.mkdtemp()
        self.log.debug("repo_dir: {}".format(repo_dir))

        reponame = f"{NAMESPACE}{reponame}"
        commands = self._commands(reponame, repo_dir)

        try:
            self.log.debug("clone the pkg repository into repo_dir directory")
            commands.clone(reponame, target=repo_dir, skip_hooks=True)
        except Exception as e:
            self.log.error("Failed to clone the Git repository and add files.")
            raise PackageImportException(str(e))

        oldpath = os.getcwd()
        self.log.debug("Switching to repo_dir: {}".format(repo_dir))
        os.chdir(repo_dir)

        self.log.debug("Setting up Git user name and email.")
        self.run_git_config(user.name, user.email)

        message = "automatic import"

        commit = None
        for branch in branches:
            self.log.debug("checkout '{0}' branch".format(branch))

            try:
                commands.switch_branch(branch)
            except rpkgError as ex:
                self.log.error(str(ex))
                continue

            try:
                if not commit:
                    upload_files = commands.import_srpm(
                        srpm_path, check_specfile_matches_repo_name=False)
                    # in case of importing, the content of directory in `reponame`
                    # changes. To update the state of `Commands` class isn't an easy
                    # process - look how much logic pyrpkg.cli.cliClient.load_cmd has
                    # and the logic is written for context in cliClient, not for Commands
                    # which we are using. Considering this wouldn't be an easy task for
                    # pyrpkg to implement, we can refresh the Commands class after every
                    # import - even if it is not elegant
                    # note: if https://pagure.io/rpkg/issue/690 is resolved, you may delete this
                    commands = self._commands(reponame, repo_dir)
                    if upload_files:
                        commands.upload(upload_files, replace=True)
                    try:
                        self.log.debug("commit")
                        commands.commit(message)
                    except rpkgError as e:
                        # Probably nothing to be committed.
                        self.log.error(str(e))
                else:
                    self.rebase(commit)
            except Exception as exc:
                self.log.exception("Error during source uploading, merge, or commit: %s", str(exc))
                continue

            try:
                self.log.debug("push")
                commands.push()
            except rpkgError as e:
                self.log.exception("Exception raised during push: %s", str(e))
                continue

            commands.load_commit()
            # branch_commits[branch] = commands.commithash

        os.chdir(oldpath)
        shutil.rmtree(repo_dir)
        return commit

    def _commands(self, repo_name, repo_dir):
        # use rpkg lib to import the source rpm
        commands = Commands(path=repo_dir,
                            lookaside="",
                            lookasidehash="sha256",
                            lookaside_cgi="",
                            gitbaseurl=GIT_BASE_URL,
                            anongiturl="",
                            branchre="",
                            kojiprofile="",
                            build_client="",
                            allow_pre_generated_srpm=True)
        commands.source_entry_type = "bsd"

        # rpkg gets module_name as a basename of git url
        # we use module_name as "username/projectname/package_name"
        # basename is not working here - so I'm setting it manually
        # commands.repo_name = repo_name

        # rpkg calls upload.cgi script on the dist git server
        # here, I just copy the source files manually with custom function
        # I also add one parameter "repo_dir" to that function with this hack
        # commands.lookasidecache.upload = types.MethodType(my_upload_fabric(opts), repo_dir)
        # commands.lookasidecache.upload = partial(my_upload, repo_dir)
        commands.lookasidecache.upload = self.my_upload
        return commands

    def my_upload(self, reponame, abs_filename, filehash, offline=False):
        """
        This is a replacement function for uploading sources.
        Rpkg uses upload.cgi for uploading which doesn't make sense
        on the local machine.
        """
        filename = os.path.basename(abs_filename)
        destination = os.path.join(LOOKASIDE_LOCATION, reponame,
                                   filename, filehash, filename)

        if not os.path.isdir(os.path.dirname(destination)):
            try:
                os.makedirs(os.path.dirname(destination))
            except OSError as ex:
                self.log.exception(str(ex))

        if not os.path.exists(destination):
            shutil.copyfile(abs_filename, destination)
