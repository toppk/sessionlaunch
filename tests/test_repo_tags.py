import tempfile
import unittest
from pathlib import Path

from session_launch.repo_tags import origin_path, repo_tag_for


class RepoTagsTest(unittest.TestCase):
    def test_origin_formats(self):
        self.assertEqual(origin_path("git@github.com:example/terminal.git"), "example/terminal")
        self.assertEqual(origin_path("https://github.com/example/terminal.git"), "example/terminal")
        self.assertEqual(origin_path("ssh://git@gitlab.com/org/team/app.git"), "org/team/app")
        self.assertIsNone(origin_path("/home/user/local.git"))

    def test_nested_directory_and_no_origin(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder) / "project"
            (repo / ".git").mkdir(parents=True)
            nested = repo / "src" / "ui"
            nested.mkdir(parents=True)
            config = repo / ".git" / "config"
            config.write_text('[remote "origin"]\nurl = git@github.com:example/terminal.git\n')
            self.assertEqual(repo_tag_for(str(nested)), "repo:example/terminal")
            config.write_text("[core]\nrepositoryformatversion = 0\n")
            repo_tag_for.cache_clear()
            self.assertEqual(repo_tag_for(str(nested)), "repo:local/project")


if __name__ == "__main__":
    unittest.main()
