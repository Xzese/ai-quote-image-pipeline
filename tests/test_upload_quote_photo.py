"""Publishing stage tests. External effects are injected test doubles."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from quote_image_generator.publishing_run import publish_one


def run(tmp_path, records, *, post=None, prepare=None, alert=None, choose=None):
    return publish_one(
        records,
        tmp_path,
        path_for=lambda root, id: root / f"{id}1024x1024.jpeg",
        choose=choose or (lambda items: items[0]),
        post=post or Mock(return_value={"id": "confirmed"}),
        prepare=prepare or Mock(),
        alert=alert or Mock(),
    )


def render(tmp_path, id="q001", content=b"fixture"):
    (tmp_path / f"{id}1024x1024.jpeg").write_bytes(content)
    return {"_id": id, "content": "Quote", "author": "Author", "hashtags": "#quote"}


def test_only_existing_renders_are_selected(tmp_path):
    present = render(tmp_path)
    missing = {"_id": "missing"}
    choose = Mock(side_effect=lambda items: items[0])
    result = run(tmp_path, [missing, present], choose=choose)
    assert result.status == "published" and result.excluded == 1
    assert len(choose.call_args.args[0]) == 1


def test_missing_images_skip_without_requesting_token(tmp_path):
    prepare, post = Mock(), Mock()
    result = run(tmp_path, [{"_id": "missing"}], prepare=prepare, post=post)
    assert result.status == "skipped" and result.exit_code == 0
    prepare.assert_not_called()
    post.assert_not_called()


def test_empty_image_is_not_eligible(tmp_path):
    assert run(tmp_path, [render(tmp_path, content=b"")]).status == "skipped"


def test_uncertain_post_is_never_retried(tmp_path):
    post = Mock(side_effect=TimeoutError("potentially sensitive URL"))
    alert = Mock()
    result = run(tmp_path, [render(tmp_path)], post=post, alert=alert)
    assert result.status == "unknown" and result.exit_code == 1
    post.assert_called_once()
    alert.assert_called_once()
    assert "sensitive" not in alert.call_args.args[1]


@pytest.mark.parametrize("response", [None, {}, {"id": ""}, {"id": 1}])
def test_missing_confirmation_is_not_success(tmp_path, response):
    post = Mock(return_value=response)
    assert run(tmp_path, [render(tmp_path)], post=post).status == "unknown"
    post.assert_called_once()


def test_typed_publisher_result_is_supported(tmp_path):
    result = run(
        tmp_path,
        [render(tmp_path)],
        post=Mock(return_value=SimpleNamespace(media_id="confirmed")),
    )
    assert result.status == "published" and result.media_id == "confirmed"


def test_token_provider_failure_prevents_publication(tmp_path):
    post = Mock()
    result = run(
        tmp_path,
        [render(tmp_path)],
        prepare=Mock(side_effect=RuntimeError()),
        post=post,
    )
    assert result.status == "failed"
    post.assert_not_called()


def test_notification_failure_does_not_repeat_publication(tmp_path):
    post = Mock(side_effect=TimeoutError())
    result = run(
        tmp_path, [render(tmp_path)], post=post, alert=Mock(side_effect=OSError())
    )
    assert result.status == "unknown"
    post.assert_called_once()


def test_prepare_precedes_one_publication(tmp_path):
    events = []

    def post(path, caption):
        events.append(("post", caption))
        return {"id": "confirmed"}

    result = run(
        tmp_path,
        [render(tmp_path)],
        prepare=lambda: events.append("prepare"),
        post=post,
    )
    assert result.exit_code == 0
    assert events == ["prepare", ("post", "#quote")]


def test_render_removed_during_preparation_is_not_posted(tmp_path):
    record = render(tmp_path)
    post = Mock()
    result = run(
        tmp_path,
        [record],
        post=post,
        prepare=lambda: (tmp_path / "q0011024x1024.jpeg").unlink(),
    )
    assert result.status == "failed"
    post.assert_not_called()


def test_symlink_outside_render_directory_is_not_eligible(tmp_path):
    directory = tmp_path / "renders"
    directory.mkdir()
    outside = tmp_path / "outside.jpeg"
    outside.write_bytes(b"fixture")
    (directory / "q0011024x1024.jpeg").symlink_to(outside)
    assert run(directory, [{"_id": "q001"}]).status == "skipped"


def test_randomizer_cannot_select_an_ineligible_file(tmp_path):
    record = render(tmp_path)
    with pytest.raises(ValueError):
        run(tmp_path, [record], choose=lambda items: ({"_id": "other"}, Path("/other")))
