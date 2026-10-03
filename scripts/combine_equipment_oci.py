#!/usr/bin/env python3
"""Combine independently qualified native images into one verified OCI layout."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

INDEX = "application/vnd.oci.image.index.v1+json"
MANIFEST = "application/vnd.oci.image.manifest.v1+json"
DOCKER_INDEX = "application/vnd.docker.distribution.manifest.list.v2+json"
DOCKER_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"


def combine(archives, destination):
    blobs, manifests = {}, []
    for architecture, path in archives.items():
        files = {}
        with tarfile.open(path) as archive:
            for member in archive:
                name = member.name.removeprefix("./")
                if member.isdir(): continue
                if not member.isfile() or not (name in {"index.json", "oci-layout"} or re.fullmatch(r"blobs/sha256/[0-9a-f]{64}", name)):
                    raise ValueError("Unexpected OCI archive member")
                if name in files: raise ValueError("Duplicate OCI archive member")
                data = archive.extractfile(member).read()
                if name.startswith("blobs/") and hashlib.sha256(data).hexdigest() != name.split("/")[-1]:
                    raise ValueError("OCI blob digest differs from its content")
                files[name] = data
        if json.loads(files["oci-layout"]).get("imageLayoutVersion") != "1.0.0":
            raise ValueError("Unsupported OCI layout version")
        visited = set()
        def blob(descriptor):
            digest = descriptor.get("digest", "")
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest): raise ValueError("Invalid OCI descriptor digest")
            data = files["blobs/sha256/" + digest.removeprefix("sha256:")]
            if len(data) != descriptor.get("size"): raise ValueError("OCI descriptor size differs from its blob")
            return data
        def walk(descriptor):
            digest = descriptor.get("digest")
            if digest in visited: raise ValueError("Repeated or recursive OCI image descriptor")
            visited.add(digest)
            value = json.loads(blob(descriptor))
            if descriptor.get("mediaType") in {INDEX, DOCKER_INDEX}:
                for child in value["manifests"]: walk(child)
            elif descriptor.get("mediaType") in {MANIFEST, DOCKER_MANIFEST}:
                config = json.loads(blob(value["config"]))
                if config.get("architecture") != architecture or config.get("os") != "linux":
                    raise ValueError("Image config differs from its qualified native platform")
                for layer in value.get("layers", []): blob(layer)
                manifests.append({**descriptor, "platform": {"architecture": architecture, "os": "linux"}})
            else: raise ValueError("Unsupported image descriptor media type")
        before = len(manifests)
        for descriptor in json.loads(files["index.json"])["manifests"]: walk(descriptor)
        if len(manifests) - before != 1: raise ValueError("Expected one image per qualified platform")
        blobs.update({name: data for name, data in files.items() if name.startswith("blobs/")})
    if set(archives) != {"amd64", "arm64"}: raise ValueError("Both native release platforms are required")
    index = json.dumps({"schemaVersion": 2, "mediaType": INDEX, "manifests": manifests}, sort_keys=True, separators=(",", ":")).encode()
    files = {**blobs, "oci-layout": b'{"imageLayoutVersion":"1.0.0"}', "index.json": index}
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with tarfile.open(temporary, "w") as archive:
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(name); info.size = len(data); info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    temporary.replace(destination)
    return "sha256:" + hashlib.sha256(index).hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--metadata", type=Path, required=True)
    args = parser.parse_args()
    digest = combine({arch: args.directory / f"{arch}.tar" for arch in ("amd64", "arm64")}, args.destination)
    args.metadata.write_text(json.dumps({"containerimage.digest": digest}) + "\n")
