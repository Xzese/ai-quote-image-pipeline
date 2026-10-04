"""Receipt-backed workflow, shared by the stage CLIs and offline demo."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import signal
import re
import threading
import time
from uuid import uuid4

from quote_image_generator.config import (
    get_env_int,
    get_env_bool,
    get_env_str,
    load_project_env,
    resolve_repo_path,
)
from quote_image_generator.quote_validation import (
    safe_output_file_path,
    validate_quote_records,
)
from quote_image_generator.run_state import (
    StateStore,
    RunBusy,
    atomic_json,
    corpus_lock,
    digest,
    file_digest,
)

PROMPT_TEMPLATE_VERSION = "visual-prompt-v2-words"
RENDER_TEMPLATE_VERSION = "positive-atmosphere-v1"
RENDER_SUFFIX = " Must have a positive, high energy atmosphere."


def validate_generated(generated):
    prompt = generated["prompt"].strip()
    if len(prompt.split()) > 50 or any(c in prompt for c in ('"', "\n", "\r")):
        raise ValueError("Prompt violates the documented word/line contract.")
    hashtags = generated["hashtags"].split()
    if not 1 <= len(hashtags) <= 20 or not all(
        re.fullmatch(r"#[A-Za-z0-9_]+", h) for h in hashtags
    ):
        raise ValueError("Hashtags violate the structured-output contract.")


def render_input(record, state, metadata):
    return digest(
        {
            "prompt": prompt_output(record),
            "source": state["source"],
            "metadata": metadata,
        }
    )


def now():
    return datetime.now(timezone.utc).isoformat()


def prompt_input(record, metadata):
    return digest(
        {
            "source": {k: record[k] for k in ("_id", "content", "author")},
            "metadata": metadata,
        }
    )


def prompt_output(record):
    return digest({k: record.get(k, "") for k in ("prompt", "hashtags")})


def run_workflow(
    corpus,
    *,
    stage="all",
    mode="batch",
    fetch=None,
    after_prompt=None,
    prompt=None,
    render=None,
    publish=None,
    prompt_metadata=None,
    render_metadata=None,
    stop_event=None,
    resource_dirs=(),
    prepare=None,
    alert=None,
    publication_dir=None,
):
    """Callbacks provide effects; this function owns persistence and eligibility.

    Publish is opt-in. Stages persist success before subsequent work. A crash
    during publishing leaves a durable unknown claim which must be reconciled.
    """
    started = time.monotonic()
    summary = {
        "version": 1,
        "run_id": uuid4().hex,
        "started_at": now(),
        "stage": stage,
        "mode": mode,
        "items": [],
        "human_review_required": True,
    }
    stop_event = stop_event or threading.Event()
    try:
        with ExitStack() as locks:
            if mode not in ("batch", "per_quote"):
                raise ValueError("PIPELINE_MODE must be batch or per_quote.")
            if fetch is not None and stage != "all":
                raise ValueError("Quote fetching requires the complete workflow.")
            locks.enter_context(corpus_lock(corpus))
            for directory in sorted(set(Path(d).resolve() for d in resource_dirs)):
                locks.enter_context(corpus_lock(directory / ".pipeline"))
            store = StateStore(corpus)
            if fetch is not None:
                from quote_image_generator.get_quotes import merge_quotes

                fetch_started = time.monotonic()
                summary["fetch"] = {"status": "running", "started_at": now()}
                try:
                    previous = (
                        json.loads(Path(corpus).read_text(encoding="utf-8"))
                        if Path(corpus).exists()
                        else []
                    )
                    validate_quote_records(previous)
                    if stop_event.is_set():
                        raise InterruptedError("Quote retrieval cancelled.")
                    fetched = fetch()
                    if stop_event.is_set():
                        raise InterruptedError("Quote retrieval cancelled.")
                    records = merge_quotes(previous, fetched)
                    atomic_json(corpus, records)
                    summary["fetch"].update(status="completed", records=len(records))
                except InterruptedError:
                    summary["fetch"]["status"] = "cancelled"
                except Exception:
                    summary["fetch"]["status"] = "failed"
                summary["fetch"]["duration_seconds"] = round(
                    time.monotonic() - fetch_started, 3
                )
                if summary["fetch"]["status"] != "completed":
                    status = summary["fetch"]["status"]
                    summary.update(
                        status=status,
                        exit_code=130 if status == "cancelled" else 1,
                        counts={status: 1},
                        duration_seconds=round(time.monotonic() - started, 3),
                    )
                    atomic_json(str(Path(corpus).resolve()) + ".summary.json", summary)
                    return summary
            else:
                records = validate_quote_records(
                    json.loads(Path(corpus).read_text(encoding="utf-8"))
                )
            summary["items"] = [{"quote_id": r["_id"]} for r in records]
            phases = (
                ("prompt", "render") if stage == "all" and mode == "batch" else (stage,)
            )
            handoff_failed = False
            for phase in phases:
                for record, outcomes in zip(records, summary["items"]):
                    state = store.item(record)
                    if stop_event.is_set():
                        outcomes[phase if phase != "all" else "cancelled"] = "cancelled"
                        continue
                    if phase in ("all", "prompt"):
                        metadata = dict(prompt_metadata or {})
                        fingerprint = prompt_input(record, metadata)
                        previous = state.get("prompt", {})
                        if (
                            previous.get("status") == "completed"
                            and previous.get("input") == fingerprint
                            and previous.get("output") == prompt_output(record)
                        ):
                            outcomes["prompt"] = "skipped"
                        else:
                            state["prompt"] = {
                                "status": "running",
                                "input": fingerprint,
                                "metadata": metadata,
                            }
                            state.pop("render", None)
                            state["prompt"]["started_at"] = now()
                            store.save()
                            stage_started = time.monotonic()
                            try:
                                generated = prompt(dict(record))
                                if not generated or not all(
                                    isinstance(generated.get(k), str)
                                    and generated[k].strip()
                                    for k in ("prompt", "hashtags")
                                ):
                                    raise ValueError(
                                        "Prompt generation did not complete both fields."
                                    )
                                validate_generated(generated)
                                record.update(
                                    {
                                        k: generated[k].strip()
                                        for k in ("prompt", "hashtags")
                                    }
                                )
                                atomic_json(corpus, records)
                                state["prompt"].update(
                                    status="completed",
                                    output=prompt_output(record),
                                    completed_at=now(),
                                    generation=generated.get("generation", {}),
                                )
                                outcomes["prompt"] = "completed"
                            except InterruptedError:
                                state["prompt"]["status"] = outcomes["prompt"] = (
                                    "cancelled"
                                )
                                stop_event.set()
                            except Exception:
                                state["prompt"]["status"] = outcomes["prompt"] = (
                                    "failed"
                                )
                            state["prompt"]["duration_seconds"] = round(
                                time.monotonic() - stage_started, 3
                            )
                            store.save()
                    if phase in ("all", "render"):
                        receipt = state.get("prompt", {})
                        if stop_event.is_set():
                            outcomes["render"] = "cancelled"
                        elif (
                            handoff_failed
                            or receipt.get("status") != "completed"
                            or receipt.get("output") != prompt_output(record)
                        ):
                            outcomes["render"] = "blocked"
                        else:
                            metadata = dict(render_metadata or {})
                            fingerprint = render_input(record, state, metadata)
                            previous = state.get("render", {})
                            if completed_render(previous, fingerprint):
                                outcomes["render"] = "skipped"
                            else:
                                state["render"] = {
                                    "status": "running",
                                    "input": fingerprint,
                                    "metadata": metadata,
                                }
                                state["render"]["started_at"] = now()
                                store.save()
                                stage_started = time.monotonic()
                                try:
                                    result = render(dict(record))
                                    path = Path(result["path"]).resolve()
                                    # Decode the whole image before declaring an artefact complete.
                                    from PIL import Image

                                    with Image.open(path) as image:
                                        image.load()
                                        if image.format != "JPEG":
                                            raise ValueError(
                                                "Final artefact must be a JPEG."
                                            )
                                    state["render"].update(
                                        result,
                                        path=str(path),
                                        status="completed",
                                        sha256=file_digest(path),
                                        completed_at=now(),
                                    )
                                    outcomes["render"] = "completed"
                                except InterruptedError:
                                    state["render"]["status"] = outcomes["render"] = (
                                        "cancelled"
                                    )
                                    stop_event.set()
                                except Exception:
                                    state["render"]["status"] = outcomes["render"] = (
                                        "failed"
                                    )
                                state["render"]["duration_seconds"] = round(
                                    time.monotonic() - stage_started, 3
                                )
                                store.save()
                if phase in ("prompt", "all") and after_prompt is not None:
                    try:
                        after_prompt()
                        summary["model_release"] = {"status": "completed"}
                    except InterruptedError:
                        stop_event.set()
                        summary["model_release"] = {"status": "cancelled"}
                    except Exception:
                        handoff_failed = True
                        summary["model_release"] = {"status": "failed"}
            if stage in ("all", "publish"):
                if stop_event.is_set():
                    summary["publish"] = {"status": "cancelled"}
                elif handoff_failed:
                    summary["publish"] = {
                        "status": "blocked",
                        "reason": "model_release_failed",
                    }
                elif publish is None:
                    summary["publish"] = {"status": "skipped", "reason": "disabled"}
                else:
                    from quote_image_generator.publishing_run import publish_one

                    result = publish_one(
                        records,
                        publication_dir,
                        path_for=None,
                        choose=lambda items: items[0],
                        post=publish,
                        prepare=prepare or (lambda: None),
                        alert=alert or (lambda *_: None),
                        store=store,
                    )
                    from dataclasses import asdict

                    summary["publish"] = asdict(result)
            statuses = [
                value
                for item in summary["items"]
                for key, value in item.items()
                if key != "quote_id"
            ]
            statuses += [summary.get("publish", {}).get("status", "skipped")]
            statuses += [
                summary[k]["status"] for k in ("fetch", "model_release") if k in summary
            ]
            counts = Counter(statuses)
            summary["counts"] = dict(sorted(counts.items()))
            bad = any(counts.get(k) for k in ("failed", "unknown", "blocked"))
            if stop_event.is_set():
                summary["status"] = "cancelled"
            elif bad:
                progress = any(
                    value in ("completed", "skipped")
                    for item in summary["items"]
                    for key, value in item.items()
                    if key != "quote_id"
                ) or counts.get("published")
                summary["status"] = "partial" if progress else "failed"
            else:
                summary["status"] = "completed"
            summary["exit_code"] = (
                130 if summary["status"] == "cancelled" else 1 if bad else 0
            )
            summary["duration_seconds"] = round(time.monotonic() - started, 3)
            summary["metrics"] = {
                "records": len(records),
                "completed_prompts": sum(
                    store.item(r).get("prompt", {}).get("status") == "completed"
                    for r in records
                ),
                "prompt_words": {
                    r["_id"]: len(r.get("prompt", "").split()) for r in records
                },
            }
            summary["eligible_renders"] = sum(
                completed_render(store.item(r).get("render", {})) for r in records
            )
            atomic_json(str(Path(corpus).resolve()) + ".summary.json", summary)
    except RunBusy:
        summary.update(status="busy", exit_code=75)
    except KeyboardInterrupt:
        summary.update(status="cancelled", exit_code=130)
    except InterruptedError:
        summary.update(status="cancelled", exit_code=130)
    except Exception:
        summary.update(
            status="failed", exit_code=1, reason="configuration_or_persistence_failure"
        )
    return summary


def completed_render(receipt, fingerprint=None):
    if receipt.get("status") != "completed" or (
        fingerprint is not None and receipt.get("input") != fingerprint
    ):
        return False
    try:
        return (
            Path(receipt["path"]).stat().st_size > 0
            and file_digest(receipt["path"]) == receipt["sha256"]
        )
    except (OSError, KeyError):
        return False


class LivePrompt:
    def __init__(self, release_ollama=False):
        self.runtime = None
        self.owned_instance = None
        self.model_lock = None
        self.ready = False
        self.release_ollama = release_ollama

    def metadata(self):
        from quote_image_generator import get_prompt as gp

        self.settings = gp._load_prompt_settings()
        return {
            "provider": self.settings["provider"],
            "endpoint_sha256": digest(self.settings["base_url"]),
            "model": self.settings["model_name"],
            "preset": self.settings["preset"],
            "template_version": PROMPT_TEMPLATE_VERSION,
            "measurement": "whitespace-words-v1",
            "max_words": gp.MAX_PROMPT_TOKENS,
            "temperature": 0.7,
            "max_output_tokens": 512,
            "context_length": self.settings["context_length"],
        }

    def __call__(self, record):
        from quote_image_generator import get_prompt as gp

        if self.runtime is None:
            s = self.settings
            if s["provider"] == "ollama":
                from quote_image_generator.ollama import OllamaClient

                if self.release_ollama:
                    self._acquire_model_lock("ollama", s["base_url"])
                self.runtime = (
                    OllamaClient(
                        s["base_url"],
                        s["context_length"],
                        gp.stop_event,
                        release_on_close=self.release_ollama,
                    ),
                    gp.create_tokenizer(),
                    None,
                )
            else:
                self._start_lm_studio(gp, s)
        if not self.ready:
            gp._validate_lm_studio_readiness(
                client=self.runtime[0],
                model_name=self.settings["model_name"],
                preset=self.settings["preset"],
            )
            self.ready = True
        client, tokenizer, _ = self.runtime
        kwargs = {
            "client": client,
            "model_name": self.settings["model_name"],
            "preset": self.settings["preset"],
        }
        generated = gp.generate_prompt(
            record, record["_id"], tokenizer=tokenizer, **kwargs
        )
        hashtags = gp.generate_hashtags(record, record["_id"], **kwargs)
        return {
            "prompt": generated,
            "hashtags": hashtags,
            "generation": {
                "provider": self.settings["provider"],
                "model": self.settings["model_name"],
                "instance_id": self.runtime[2],
                "created_at": now(),
            },
        }

    def _start_lm_studio(self, gp, s):
        self._acquire_model_lock("lm", s["native_api_base_url"])
        instance = gp.ensure_lm_studio_model(
            native_api_base_url=s["native_api_base_url"],
            api_key=s["api_key"],
            model_name=s["model_name"],
            context_length=s["context_length"],
            on_load=lambda identifier: setattr(self, "owned_instance", identifier),
        )
        self.runtime = (
            gp.create_openai_client(s["base_url"], s["api_key"]),
            gp.create_tokenizer(),
            instance,
        )

    def _acquire_model_lock(self, provider, endpoint):
        from filelock import FileLock

        lock_path = resolve_repo_path(
            "output/locks/" + provider + "-" + digest(endpoint) + ".lock"
        )
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        if self.model_lock is None:
            self.model_lock = FileLock(lock_path, timeout=0)
        if not self.model_lock.is_locked:
            self.model_lock.acquire()

    def close(self):
        try:
            if self.runtime and hasattr(self.runtime[0], "close"):
                self.runtime[0].close()
            if self.owned_instance:
                from quote_image_generator import get_prompt as gp
                import requests

                with requests.Session() as session:
                    gp._unload_lm_studio_model(
                        session=session,
                        native_api_base_url=self.settings["native_api_base_url"],
                        api_key=self.settings["api_key"],
                        instance_id=self.owned_instance,
                    )
        finally:
            self.runtime = None
            self.owned_instance = None
            self.ready = False
            if self.model_lock:
                self.model_lock.release()


class LiveRender:
    def __init__(self, stop_event):
        from quote_image_generator import get_image as gi

        self.gi = gi
        self.stop_event = stop_event
        self.width = get_env_int("COMFYUI_WIDTH", gi.WIDTH)
        self.height = get_env_int("COMFYUI_HEIGHT", gi.HEIGHT)
        self.steps = get_env_int("COMFYUI_STEPS", gi.STEPS)
        self.cfg = float(get_env_str("COMFYUI_CFG", str(gi.CFG)))
        if any(
            value is None or value <= 0
            for value in (self.width, self.height, self.steps)
        ):
            raise ValueError(
                "COMFYUI_WIDTH, COMFYUI_HEIGHT and COMFYUI_STEPS must be positive."
            )
        if self.width % 8 or self.height % 8:
            raise ValueError("ComfyUI dimensions must be multiples of 8.")
        if not math.isfinite(self.cfg) or self.cfg < 0:
            raise ValueError("COMFYUI_CFG must be finite and nonnegative.")
        self.workflow = json.loads(
            gi.resolve_workflow_path(get_env_str("COMFYUI_WORKFLOW_PATH")).read_text()
        )
        gi.validate_workflow_shape(self.workflow)
        self.seed = get_env_int("GENERATION_SEED", 42)
        if self.seed is None or not 0 <= self.seed <= 9_007_199_254_740_990:
            raise ValueError("GENERATION_SEED is out of range.")
        self.timeout = get_env_int("COMFYUI_DEADLINE_SECONDS", 300)
        if self.timeout is None or self.timeout <= 0:
            raise ValueError("COMFYUI_DEADLINE_SECONDS must be positive.")
        self.url = get_env_str("COMFYUI_URL", "http://127.0.0.1:8000")
        self.base_dir = resolve_repo_path(
            get_env_str("OUTPUT_IMAGE_PATH", "output/images")
        )
        self.final_dir = resolve_repo_path(
            get_env_str("OVERLAY_OUTPUT_PATH", "output/images_text_overlay")
        )

    def metadata(self):
        models = {
            node_id: {
                key: value
                for key, value in node.get("inputs", {}).items()
                if key.endswith("_name")
            }
            for node_id, node in self.workflow.items()
            if isinstance(node, dict)
            and any(key.endswith("_name") for key in node.get("inputs", {}))
        }
        return {
            "workflow_sha256": digest(self.workflow),
            "models": models,
            "seed": self.seed,
            "width": self.width,
            "height": self.height,
            "steps": self.steps,
            "cfg": self.cfg,
            "template_version": RENDER_TEMPLATE_VERSION,
            "font_sha256": file_digest(self.gi.FONT_FILE),
            "overlay_version": "alegreya-v1",
            "renderer_sha256": file_digest(Path(self.gi.__file__)),
            "deadline_seconds": self.timeout,
        }

    def __call__(self, record):
        from quote_image_generator.deadline import Deadline, request
        import requests

        gi = self.gi
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.final_dir.mkdir(parents=True, exist_ok=True)
        png = safe_output_file_path(
            self.base_dir, record["_id"], self.width, self.height, "png"
        )
        jpg = safe_output_file_path(
            self.final_dir, record["_id"], self.width, self.height, "jpeg"
        )
        workflow = gi.build_workflow(
            self.workflow,
            record["prompt"] + RENDER_SUFFIX,
            seed=self.seed,
            width=self.width,
            height=self.height,
            steps=self.steps,
            cfg=self.cfg,
        )
        deadline = Deadline(self.timeout)

        def post(url, **kwargs):
            kwargs.pop("timeout", None)
            return request(
                "POST", url, deadline=deadline, stop_event=self.stop_event, **kwargs
            )

        def get(url, **kwargs):
            kwargs.pop("timeout", None)
            return request(
                "GET", url, deadline=deadline, stop_event=self.stop_event, **kwargs
            )

        with requests.Session() as session:
            prompt_id = gi.queue_prompt(session, self.url, workflow, post_request=post)
            try:
                image = gi.wait_for_image(
                    session,
                    self.url,
                    prompt_id,
                    stop_event=self.stop_event,
                    get_request=get,
                    deadline=deadline,
                )
                gi.download_image(session, self.url, image, png, get_request=get)
            except BaseException:
                # Delete only this known job. The global interrupt API races with other owners.
                try:
                    request(
                        "POST",
                        self.url + "/queue",
                        deadline=Deadline(2),
                        json={"delete": [prompt_id]},
                    )
                except Exception:
                    pass
                raise
        from PIL import Image

        with Image.open(png) as base:
            base.load()
            if base.size != (self.width, self.height):
                raise ValueError("Unexpected render dimensions.")
        temp = jpg.with_suffix(".part")
        try:
            gi.overlay_text_on_image(png, temp, record["content"], record["author"])
            temp.replace(jpg)
        finally:
            temp.unlink(missing_ok=True)
        return {
            "path": str(jpg),
            "prompt_id": prompt_id,
            "generation_workflow_sha256": digest(workflow),
            "seed": self.seed,
            "generation_prompt": record["prompt"] + RENDER_SUFFIX,
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage", choices=("all", "prompt", "render", "publish"), default="all"
    )
    parser.add_argument(
        "--publish", action="store_true", help="Enable one durable publication attempt."
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Run the fixed corpus without network or credentials.",
    )
    parser.add_argument("--output", type=Path, default=Path("output/demo"))
    parser.add_argument(
        "--mode",
        choices=("batch", "per_quote"),
        help="Override PIPELINE_MODE (default: batch).",
    )
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Use the saved quote corpus without refreshing Quotable.",
    )
    args = parser.parse_args(argv)
    if args.publish and args.stage not in ("all", "publish"):
        parser.error("--publish requires --stage all or --stage publish.")
    if args.offline and args.stage != "all":
        parser.error("--offline runs the complete fixture workflow; omit --stage.")
    load_project_env()
    if args.offline:
        if args.publish or args.stage == "publish":
            parser.error("Offline mode never contacts a publishing provider.")
        from quote_image_generator.offline import demo

        try:
            mode = args.mode or get_env_str("PIPELINE_MODE", "batch")
            result = demo(args.output, mode=mode)
        except RunBusy:
            result = {"status": "busy", "exit_code": 75}
        except Exception:
            result = {
                "status": "failed",
                "exit_code": 1,
                "reason": "offline_configuration_failure",
            }
    else:
        stop = threading.Event()
        gp = None
        if args.stage in ("all", "prompt"):
            from quote_image_generator import get_prompt as gp

            gp.stop_event.clear()

        def cancel(*_):
            stop.set()
            if gp is not None:
                gp.stop_event.set()

        signal.signal(signal.SIGINT, cancel)
        signal.signal(signal.SIGTERM, cancel)
        prompt_adapter = None
        try:
            mode = args.mode or get_env_str("PIPELINE_MODE", "batch")
            if mode not in ("batch", "per_quote"):
                raise ValueError("PIPELINE_MODE must be batch or per_quote.")
            prompt_adapter = LivePrompt(
                release_ollama=mode == "batch" or args.stage == "prompt"
            )
            corpus = resolve_repo_path(
                get_env_str("QUOTES_FILE_PATH", "output/quotes.json")
            )
            pm = prompt_adapter.metadata() if args.stage in ("all", "prompt") else None
            render_adapter = (
                LiveRender(stop) if args.stage in ("all", "render") else None
            )
            publisher = prepare = alert = None
            fetch = None
            if (
                args.stage == "all"
                and not args.skip_fetch
                and get_env_bool("PIPELINE_FETCH_QUOTES", True)
            ):
                from quote_image_generator.get_quotes import retrieve_quotes

                def fetch():
                    return retrieve_quotes(stop_event=stop)

            if args.publish or args.stage == "publish":
                from quote_image_generator.publisher import (
                    publish as publisher,
                    prepare,
                    alert,
                )
            result = run_workflow(
                corpus,
                stage=args.stage,
                mode=mode,
                fetch=fetch,
                after_prompt=prompt_adapter.close,
                prompt=prompt_adapter,
                render=render_adapter,
                publish=publisher,
                prepare=prepare,
                alert=alert,
                prompt_metadata=pm,
                render_metadata=render_adapter.metadata() if render_adapter else None,
                stop_event=stop,
                publication_dir=resolve_repo_path(
                    get_env_str("OVERLAY_OUTPUT_PATH", "output/images_text_overlay")
                ),
                resource_dirs=[
                    resolve_repo_path(
                        get_env_str("OUTPUT_IMAGE_PATH", "output/images")
                    ),
                    resolve_repo_path(
                        get_env_str("OVERLAY_OUTPUT_PATH", "output/images_text_overlay")
                    ),
                ],
            )
        except Exception:
            result = {
                "status": "failed",
                "exit_code": 1,
                "reason": "configuration_failure",
            }
        finally:
            try:
                if prompt_adapter is not None:
                    prompt_adapter.close()
            except Exception:
                pass
    print(json.dumps(result, sort_keys=True))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
