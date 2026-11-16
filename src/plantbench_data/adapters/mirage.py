"""Preserve real consultation text separately from reconstructed benchmark items."""
import collections
import hashlib
import io
import os
from pathlib import Path

from ..common import read_json


def text_record(ctx, key, subject, text, provenance, role, origin, index=None):
    env = ctx.envelope("text_turn", key, [provenance])
    return ctx.add("text_turns", {**env, "turn_id": env["record_id"], "subject_ref": subject,
        "original_role": role, "turn_index": index, "text": text, "times": {},
        "text_origin": origin, "access_class": "restricted_raw" if origin != "reference_answer" else "reference_annotation"})


def annotation(ctx, key, subject, value, provenance, annotation_type, assertion_scope=None):
    env = ctx.envelope("annotation", key, [provenance])
    return ctx.add("annotations", {**env, "annotation_id": env["record_id"], "subject_ref": subject,
        "annotation_type": annotation_type, "source_label": value, "canonical_concept_id": None,
        "mapping_status": "unmapped", "assertion_status": "not_assessed",
        "assertion_scope": assertion_scope or "Source benchmark field; not an independently verified observation or agricultural gold",
        "verification_scope": "unverified", "evidence_kind": "source_annotation"})


def extract_image(ctx, key, content, provenance, cache):
    from PIL import Image
    digest = hashlib.sha256(content).hexdigest()
    if digest not in cache:
        with Image.open(io.BytesIO(content)) as im:
            im.verify()
        with Image.open(io.BytesIO(content)) as im:
            for frame in range(getattr(im, "n_frames", 1)):
                im.seek(frame)
                im.load()
            width, height, channels = im.width, im.height, len(im.getbands())
            fmt, mode, pages = im.format, im.mode, getattr(im, "n_frames", 1)
        extension = {"JPEG": ".jpg", "PNG": ".png", "TIFF": ".tiff", "WEBP": ".webp", "GIF": ".gif"}.get(fmt, ".bin")
        target = ctx.data_root / "source_media" / ctx.dataset / ctx.release / (digest + extension)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            # Exact Arrow image bytes, not recompressed or redacted source data.
            import uuid
            temporary = target.with_suffix(target.suffix + "." + uuid.uuid4().hex + ".part")
            with temporary.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, target)
            temporary.unlink()
        elif target.read_bytes() != content:
            raise ValueError("Content-addressed source media collision")
        cache[digest] = (target, width, height, channels, fmt, mode, pages)
    target, width, height, channels, fmt, mode, pages = cache[digest]
    depth = 16 if mode.startswith("I;16") else 32 if mode in {"I", "F"} else 1 if mode == "1" else 8
    env = ctx.envelope("asset", key, [provenance])
    return ctx.add("assets", {**env, "asset_id": env["record_id"], "content_sha256": digest,
        "media_type": Image.MIME.get(fmt, "application/octet-stream"), "source_relative_path": key,
        "storage_uri": str(target.relative_to(ctx.data_root)), "width": width, "height": height,
        "channels": channels, "dtype": "float32" if mode == "F" else "int32" if mode == "I" else f"uint{depth}",
        "bit_depth": depth, "page_count": pages, "modality": "rgb", "subject_scope": "consultation_case",
        "localization_status": "unresolved", "times": {}, "availability": "available", "decode_status": "decoded",
        "license_chain_refs": [f"{ctx.dataset}/{ctx.release}/repository-license"], "privacy_status": "not_reviewed",
        "access_class": "restricted_raw", "source_fields": {"byte_extraction": "unchanged Arrow embedded bytes", "pillow_mode": mode,
        "image_slot_is_not_time": True, "biological_entity_count": None}})


class MirageAdapter:
    def convert(self, ctx):
        import pyarrow as pa
        original_path = "source_data_for_mmmt.json"
        original = read_json(ctx.source_root / original_path)
        cases, counts, cache = {}, collections.Counter(), {}
        original_ids, split_membership = set(), collections.defaultdict(set)
        for row_index, row in enumerate(original):
            if set(row) != {"id", "title", "question", "responses"} or not isinstance(row["id"], str):
                raise ValueError("Original consultation schema changed")
            case = row["id"]
            if case in original_ids:
                raise ValueError("Duplicate original consultation ID requires review")
            original_ids.add(case)
            prov = ctx.provenance(original_path, "json", pointer=f"/{row_index}")
            subject = ctx.subject("case/" + case, "consultation_case", prov, resolution="source_group",
                                  source_fields={"source_case_id": case, "biological_individual_count": None})
            cases[case] = subject
            qprov = ctx.provenance(original_path, "json", pointer=f"/{row_index}/question", character_start=0, character_end=len(row["question"]))
            question = text_record(ctx, f"original/{case}/question", subject, row["question"], qprov, "source_question_author", "source_original_turn", 0)
            ctx.capabilities[(subject, "consultation.original_turns", "consultation_case", "user_report", "not_applicable", False)].append((question, qprov))
            annotation(ctx, f"original/{case}/title", subject, row["title"], prov, "source_case_title")
            for index, response in enumerate(row["responses"], 1):
                if set(response) != {"responder", "response"} or not isinstance(response["response"], str):
                    raise ValueError("Original response schema changed")
                rprov = ctx.provenance(original_path, "json", pointer=f"/{row_index}/responses/{index-1}/response",
                                       character_start=0, character_end=len(response["response"]))
                # Names/HTML remain in restricted raw. Do not infer expert/user
                # role or expose identity in a role label.
                turn = text_record(ctx, f"original/{case}/response/{index}", subject, response["response"], rprov,
                                   "source_responder_unresolved", "source_original_turn", index)
                ctx.capabilities[(subject, "consultation.original_turns", "consultation_case", "source_annotation", "not_applicable", False)].append((turn, rprov))
                counts["original_responses"] += 1
            counts["original_cases"] += 1
        active, unlisted = set(), []
        for relative in sorted(ctx.files):
            if relative.endswith("/state.json"):
                for entry in read_json(ctx.source_root / relative)["_data_files"]:
                    active.add(str(Path(relative).parent / entry["filename"]))
        for relative in sorted(ctx.files):
            if not relative.endswith(".arrow"):
                continue
            configuration, split = Path(relative).parts[:2]
            ctx.current_split = configuration + "/" + split
            if relative not in active:
                unlisted.append(relative)
            with pa.memory_map(str(ctx.source_root / relative), "r") as stream:
                try:
                    reader = pa.ipc.open_file(stream)
                    batches = (reader.get_batch(i) for i in range(reader.num_record_batches))
                except pa.ArrowInvalid:
                    stream.seek(0)
                    reader = pa.ipc.open_stream(stream)
                    batches = iter(reader)
                for batch_index, batch in enumerate(batches):
                    for row_index, row in enumerate(batch.to_pylist()):
                        case = row["id"]
                        if not isinstance(case, str) or not case:
                            raise ValueError("Source case identity missing")
                        key = f"{relative}/batch-{batch_index}/row-{row_index}"
                        prov = ctx.provenance(relative, "arrow", batch=batch_index, row=row_index)
                        if case not in cases:
                            cases[case] = ctx.subject("case/" + case, "consultation_case", prov, resolution="source_group",
                                                     source_fields={"source_case_id": case, "biological_individual_count": None})
                        subject = cases[case]
                        split_membership[case].add(ctx.current_split)
                        if configuration.startswith("MMST"):
                            text_record(ctx, key + "/question", subject, row["question"], prov, "source_benchmark_question", "source_reconstruction")
                            text_record(ctx, key + "/answer", subject, row["answer"], prov, "source_reference_answer", "reference_answer")
                            reference = {k:v for k,v in row.items() if k not in {"id", "question", "answer", "image_1", "image_2", "image_3"}}
                        else:
                            text_record(ctx, key + "/context", subject, row["dialog_context"], prov, "source_benchmark_context", "source_reconstruction")
                            text_record(ctx, key + "/utterance", subject, row["utterance"], prov, "source_reference_utterance", "reference_answer")
                            reference = {k:v for k,v in row.items() if k not in {"id", "dialog_context", "utterance", "image_1", "image_2", "image_3"}}
                        annotation(ctx, key + "/protected_source_fields", subject, reference, prov, "source_benchmark_fields")
                        for column in ("image_1", "image_2", "image_3"):
                            value = row.get(column)
                            if value is None:
                                counts["empty_image_slots"] += 1
                                continue
                            iprov = ctx.provenance(relative, "arrow", batch=batch_index, row=row_index, column=column)
                            if not value.get("bytes"):
                                annotation(ctx, key + "/" + column + "/unresolved", subject,
                                           {"path": value.get("path"), "availability": "missing_asset"}, iprov, "unresolved_source_image_reference")
                                counts["missing_image_bytes"] += 1
                                continue
                            asset = extract_image(ctx, key + "/" + column, value["bytes"], iprov, cache)
                            ctx.observation(key + "/" + column, subject, "visual.rgb", {"kind": "asset_ref", "value": asset},
                                            column, None, iprov, "consultation_case", "visual_asset")
                            counts["embedded_image_references"] += 1
                        counts["arrow_rows"] += 1
                        counts["configuration/" + configuration + "/" + split] += 1
                        counts["arrow_rows_linked_to_original_case"] += case in original_ids
        ctx.current_split = None
        return {"source_record_definition": "one original consultation object or one released Arrow row",
                "source_records_seen": counts["original_cases"] + counts["arrow_rows"],
                "converted": counts["original_cases"] + counts["arrow_rows"], "preserved_unmapped": 0,
                "quarantined": 0, "rejected": 0, "counts": dict(counts), "unique_source_cases": len(cases),
                "unique_embedded_image_bytes": len(cache), "released_arrow_files_not_in_state_json": unlisted,
                "inventory_policy": "All released Arrow files retained, including extra single-shard files contributing to README counts",
                "source_split_membership": {case: sorted(splits) for case, splits in split_membership.items()},
                "privacy": "All source text/media restricted; source labels reference-protected; no public text export",
                "review_required": ["Responder roles need review; no inferred expert assertions", "No image-slot chronology", "No synthetic user reports from known/missing goals"]}
