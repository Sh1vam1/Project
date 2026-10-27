"""Command line behaviour that the user relies on directly."""
from vivaah import analysis, cli, qa


def test_preview_analyses_the_photo_once(jpeg_folder, tmp_path, monkeypatch):
    calls = []
    real = analysis.analyze
    monkeypatch.setattr(analysis, "analyze", lambda photo: calls.append(1) or real(photo))
    out = tmp_path / "sheet.jpg"
    argv = ["preview", str(jpeg_folder / "a_day.jpg"), "--looks", "signature,true-colour,portra-film",
            "--out", str(out), "--size", "200"]
    assert cli.main(argv) == 0
    assert len(calls) == 1 and out.exists()


def test_learn_with_out_prints_a_command_that_finds_the_look(jpeg_folder, tmp_path, capsys):
    looks = tmp_path / "my-looks"
    assert cli.main(["learn", str(jpeg_folder), "--name", "Test Style", "--out", str(looks)]) == 0
    hint = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("Use it with:"))
    assert f'--looks-dir "{looks}"' in hint and "--look test-style" in hint
    assert cli.main(["--looks-dir", str(looks), "looks"]) == 0
    assert "test-style" in capsys.readouterr().out


def test_qa_uses_extra_look_folders(monkeypatch, tmp_path):
    seen = {}

    def fake_run(inputs, **kw):
        seen.update(kw)
        return tmp_path / "report.html", []

    monkeypatch.setattr(qa, "run", fake_run)
    assert cli.main(["--looks-dir", str(tmp_path), "qa", "--look", "my-style"]) == 0
    assert seen["looks_dirs"] == [str(tmp_path)] and seen["look_id"] == "my-style"
