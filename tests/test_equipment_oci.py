import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("combine_equipment_oci", Path(__file__).parents[1] / "scripts/combine_equipment_oci.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def native_archive(path, architecture, *, corrupt=False, extra=False):
    files = {"oci-layout": b'{"imageLayoutVersion":"1.0.0"}'}
    def blob(value, media_type):
        data = json.dumps(value, separators=(",", ":")).encode()
        digest = hashlib.sha256(data).hexdigest()
        files["blobs/sha256/" + digest] = data
        return {"mediaType": media_type, "digest": "sha256:" + digest, "size": len(data)}
    config = blob({"architecture": architecture, "os": "linux"}, "application/vnd.oci.image.config.v1+json")
    manifest = blob({"schemaVersion": 2, "config": config, "layers": []}, module.MANIFEST)
    files["index.json"] = json.dumps({"schemaVersion": 2, "manifests": [manifest]}).encode()
    if corrupt: files["blobs/sha256/" + config["digest"].split(":")[1]] = b"corrupt"
    if extra: files["../unsafe"] = b"invalid"
    with tarfile.open(path, "w") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name); info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


class NativeOciBundleTests(unittest.TestCase):
    def test_both_qualified_platforms_share_one_digest_verified_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {arch: root / (arch + ".tar") for arch in ("amd64", "arm64")}
            for arch, path in paths.items(): native_archive(path, arch)
            digest = module.combine(paths, root / "release.tar")
            with tarfile.open(root / "release.tar") as archive:
                index = archive.extractfile("index.json").read()
                self.assertEqual(digest, "sha256:" + hashlib.sha256(index).hexdigest())
                self.assertEqual({row["platform"]["architecture"] for row in json.loads(index)["manifests"]}, {"amd64", "arm64"})
                for member in archive:
                    if member.name.startswith("blobs/"):
                        self.assertEqual(hashlib.sha256(archive.extractfile(member).read()).hexdigest(), member.name.split("/")[-1])

    def test_mislabeled_corrupt_or_unsafe_native_artifacts_fail_closed(self):
        for options in [{"architecture": "amd64"}, {"architecture": "arm64", "corrupt": True}, {"architecture": "arm64", "extra": True}]:
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                native_archive(root / "amd64.tar", "amd64")
                native_archive(root / "arm64.tar", **options)
                with self.assertRaises(ValueError): module.combine({arch: root / (arch + ".tar") for arch in ("amd64", "arm64")}, root / "release.tar")
                self.assertFalse((root / "release.tar").exists())

    def test_single_platform_cannot_claim_multi_platform_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native_archive(root / "amd64.tar", "amd64")
            with self.assertRaises(ValueError): module.combine({"amd64": root / "amd64.tar"}, root / "release.tar")
