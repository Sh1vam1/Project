# Vivaah and Python Docker example

## Vivaah: wedding photo editing

[Vivaah](vivaah/) is a local wedding-photo editing tool focused on Indian
weddings. It analyses photos, applies colour corrections and looks, writes
editable Lightroom XMP sidecars, and exports JPEGs or TIFFs.

The complete application, tests, bundled model and documentation are in
[`vivaah/`](vivaah/). Start with the [Vivaah user guide](vivaah/README.md),
[Lightroom workflow](vivaah/docs/LIGHTROOM_WORKFLOW.md), or
[roadmap](vivaah/docs/ROADMAP.md).

After cloning or downloading this repository, open Terminal in the repository
folder and run:

```bash
cd vivaah
bash setup_mac.sh
```

Keep that `vivaah/` folder as the working directory for the commands in its
user guide. The Mac launcher is
[`vivaah/mac/Vivaah Grade.command`](vivaah/mac/Vivaah%20Grade.command).

Vivaah was copied from [Sh1vam1/Testlab](https://github.com/Sh1vam1/Testlab)
at commit [d2ec540](https://github.com/Sh1vam1/Testlab/commit/d2ec5402efe5bf612bf71f8960c4d9d74d204145).
Its original commits and authorship are retained in this repository's history.

## Python Docker example

The original Python Docker example remains in the repository root. It follows
the [official Docker Python development guide](https://docs.docker.com/language/python/develop/).
See [README.Docker.md](README.Docker.md) for its container instructions.
