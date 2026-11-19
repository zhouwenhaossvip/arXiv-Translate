import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arxiv_translate.desktop import (acquire_instance_lock, bundled_root,
                                     check_dependencies, configure_dependency_path,
                                     ready_to_translate, user_data_root)
from arxiv_translate.web_app import TaskApp


class DesktopTests(unittest.TestCase):
    def test_separates_installed_assets_from_user_data(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            assets = base / "installed" / "web"
            assets.mkdir(parents=True)
            data = base / "profile"
            with patch.dict(os.environ, {"LOCALAPPDATA": str(data)}):
                app = TaskApp(user_data_root(), web=assets)
            self.assertEqual(app.web, assets.resolve())
            self.assertEqual(app.root, (data / "arXiv-Translate").resolve())

    def test_bundled_root_uses_pyinstaller_extraction_path(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("arxiv_translate.desktop.sys._MEIPASS", directory, create=True):
                self.assertEqual(bundled_root(), Path(directory).resolve())

    def test_missing_compiler_disables_translation(self):
        with tempfile.TemporaryDirectory() as directory:
            with (patch("arxiv_translate.desktop.probe_latexmk", return_value=(None, "missing perl")),
                  patch("arxiv_translate.desktop.shutil.which", return_value=None),
                  patch.dict(os.environ, {"WINDIR": directory})):
                checks = check_dependencies()
            self.assertFalse(ready_to_translate(checks))
            self.assertIn("missing perl", checks[0].detail)

    def test_ready_compiler_and_font_enables_translation(self):
        with tempfile.TemporaryDirectory() as directory:
            fonts = Path(directory) / "Fonts"
            fonts.mkdir()
            (fonts / "simsun.ttc").write_bytes(b"font")
            with (patch("arxiv_translate.desktop.probe_latexmk", return_value=("latexmk", "v1")),
                  patch("arxiv_translate.desktop.shutil.which", side_effect=lambda name: name),
                  patch.dict(os.environ, {"WINDIR": directory})):
                checks = check_dependencies()
            self.assertTrue(ready_to_translate(checks))

    def test_user_font_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fonts = root / "Microsoft" / "Windows" / "Fonts"
            fonts.mkdir(parents=True)
            (fonts / "NotoSerifSC-VF.ttf").write_bytes(b"font")
            with (patch("arxiv_translate.desktop.probe_latexmk", return_value=("latexmk", "v1")),
                  patch("arxiv_translate.desktop.shutil.which", side_effect=lambda name: name),
                  patch.dict(os.environ, {"WINDIR": str(root / "windows"), "LOCALAPPDATA": str(root)})):
                self.assertTrue(ready_to_translate(check_dependencies()))

    def test_portable_perl_and_per_user_miktex_are_added_to_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            perl = root / "app" / "tools" / "strawberry" / "perl" / "bin"
            miktex = root / "profile" / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64"
            perl.mkdir(parents=True)
            miktex.mkdir(parents=True)
            with (patch("arxiv_translate.desktop.sys.frozen", True, create=True),
                  patch("arxiv_translate.desktop.sys.executable", str(root / "app" / "arXiv-Translate.exe")),
                  patch.dict(os.environ, {"LOCALAPPDATA": str(root / "profile"), "PATH": "C:\\Windows"})):
                configure_dependency_path()
                parts = os.environ["PATH"].split(os.pathsep)
                self.assertIn(str(perl), parts)
                self.assertIn(str(miktex), parts)
                configure_dependency_path()
                self.assertEqual(parts, os.environ["PATH"].split(os.pathsep))

    @unittest.skipUnless(os.name == "nt", "Windows file locking")
    def test_second_desktop_instance_cannot_share_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = acquire_instance_lock(root)
            self.assertIsNotNone(first)
            try:
                self.assertIsNone(acquire_instance_lock(root))
            finally:
                import msvcrt

                first.seek(0)
                msvcrt.locking(first.fileno(), msvcrt.LK_UNLCK, 1)
                first.close()


if __name__ == "__main__":
    unittest.main()
