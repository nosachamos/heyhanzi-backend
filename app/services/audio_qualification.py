"""Exact-file human exceptions; automated qualification remains the default."""

import json
import re
from pathlib import Path

APPROVALS = json.loads(
    (Path(__file__).resolve().parents[1] / "data/sentence-audio/manual-approvals.json").read_text()
)


def recording_approval(manifest):
    approval = manifest.get("humanApproval")
    if (
        approval not in APPROVALS
        or manifest.get("reviewStatus") != "human-approved"
        or manifest.get("humanReviewed") is not True
        or manifest["quality"].get("accepted") is not False
        or manifest["quality"].get("failures") != ["transcript-mismatch"]
    ):
        return None
    for manifest_key, approval_key in [
        ("id", "contentId"), ("voiceId", "voiceId"), ("voiceVersion", "voiceVersion"),
        ("sha256", "sha256"), ("text", "text"), ("pinyin", "pinyin"),
    ]:
        if manifest.get(manifest_key) != approval[approval_key]:
            return None
    return approval if approval["waivedFailures"] == ["transcript-mismatch"] else None


PROCESSED_DUAN = {'contentId': 'eb065f3e9106ece4a3ca0e954b79d3a9958b3de146fed7c6dae152b6330f3605', 'generationId': 'eb065f3e9106ece4a3ca0e954b79d3a9958b3de146fed7c6dae152b6330f3605', 'recordingId': 'eb065f3e9106ece4a3ca0e954b79d3a9958b3de146fed7c6dae152b6330f3605', 'voiceId': 'mandarin-male', 'voiceVersion': 'd74a42ec22281980e681667b3869f5bb87a8d01dea370f1c910d832f8f44a08f', 'sha256': '04c872cde5170bde2fc704083d2759a5993a45a9670e67f44a2869398395be6b', 'provenanceSha256': '7089ac5fb253f85992a094f60b17592456cc5c883c3269e4d57431d08f52eabf', 'parentId': 'bbdf7dafc1daa59f998e4cf02296abc3d813ee6580a5c25e184653661932b7d6', 'parentReceiptSha256': '5d1f6271ea55ebfb2070ff8c04b2ee6e99ee96ca38514df2af3c5ab23266c85d', 'parentMp3Sha256': '0dc12dbbf81cca636681e7c109f36f2b18c8bb3f52672c87a575dc21c22e00e7', 'reviewStatus': 'processed-derivative-acoustic-pass', 'normalGeneratorQualified': False, 'failedChecks': ['incomplete-generation']}
PROCESSED_PROFILES = {'male': 'd74a42ec22281980e681667b3869f5bb87a8d01dea370f1c910d832f8f44a08f', 'female': '47a4e2b453fd35ad71fdfe8bcd1739bb1b4abe41fa6ce3475ec883bf112586b1'}

def processed_release_records_valid(release, records):
    """One exact offline crop, independently checked before offline publication.

    Its preserved structural failure is not a generator pass or an owner take.
    No diagnostic paths or source recordings enter this public runtime summary.
    """
    if records != [PROCESSED_DUAN] or records[0].get("normalGeneratorQualified") is not False:
        return False
    if (release.get("profiles") != PROCESSED_PROFILES
            or release.get("qualified") is not False
            or release.get("automatedAccepted") is not False
            or type(release.get("contents")) is not int or release["contents"] < 1):
        return False
    identity = (PROCESSED_DUAN["contentId"], PROCESSED_DUAN["voiceId"])
    owners, approvals = release.get("ownerSelections", []), release.get("humanApprovals", [])
    if not isinstance(owners, list) or not isinstance(approvals, list):
        return False
    content_ids = {identity[0]}
    voice_counts = {"mandarin-male": 1, "mandarin-female": 0}
    for row in owners + approvals:
        if not isinstance(row, dict):
            return False
        content_id, voice = row.get("contentId"), row.get("voiceId", "mandarin-male")
        if not isinstance(content_id, str) or not isinstance(voice, str) or voice not in voice_counts:
            return False
        if (content_id, voice) == identity:
            return False
        content_ids.add(content_id)
        voice_counts[voice] += 1
    return len(content_ids) <= release["contents"] and max(voice_counts.values()) <= release["contents"]


def _legacy_release_is_qualified(release):
    """Accept a complete delivery under strict QA or explicit recorded exceptions.

    The historical function name is retained for callers. An owner-selected
    release is available under its authorization, never labelled fully qualified.
    Offline publication verifies every exact receipt and waveform before emitting
    this compact runtime summary; request handlers never load speech models.
    """
    if not release.get("complete"):
        return False
    processed_count = release.get("processedRecordings", 0)
    processed = release.get("processedSelections", [])
    if (type(processed_count) is not int or processed_count < 0
            or not isinstance(processed, list) or processed_count != len(processed)
            or (processed_count and not processed_release_records_valid(release, processed))):
        return False
    owner_count = release.get("ownerSelectedRecordings", 0)
    owners = release.get("ownerSelections", [])
    if (type(owner_count) is not int or owner_count < 0 or not isinstance(owners, list)
            or owner_count != len(owners)):
        return False
    if release.get("automatedAccepted") is True:
        return not release.get("humanApprovals") and not owner_count and not processed_count
    approvals = release.get("humanApprovals", [])
    if (not isinstance(approvals, list) or not (approvals or owner_count or processed_count)
            or any(approval not in APPROVALS for approval in approvals)):
        return False
    if owner_count:
        policy = release.get("ownerSelectionPolicy", {})
        profiles = release.get("profiles", {})
        if not isinstance(profiles, dict):
            return False
        voice_versions = {"mandarin-female": profiles.get("female"),
                          "mandarin-male": profiles.get("male")}
        per_voice = policy == {"version": "owner-best-available-v1",
                               "authorization": "course-rollout-owner-2026-10-22",
                               "failureLimit": 10, "voices": voice_versions}
        historical_male = policy == {"version": "owner-best-available-v1",
                                     "authorization": "course-rollout-owner-2026-10-22",
                                     "failureLimit": 10, "voiceId": "mandarin-male",
                                     "voiceVersion": profiles.get("male")}
        if (release.get("availableUnderOwnerAuthorization") is not True
                or release.get("qualified") is not False
                or not (per_voice or historical_male)
                or not re.fullmatch(r"[a-f0-9]{64}", str(profiles.get("male", "")))
                or (per_voice and (type(release.get("contents")) is not int
                    or release["contents"] <= 0
                    or any(not re.fullmatch(r"[a-f0-9]{64}", str(value))
                           for value in voice_versions.values())))):
            return False
        identities = set()
        voice_counts = {voice: 0 for voice in voice_versions}
        for selected in owners:
            if (not isinstance(selected, dict)
                    or any(not re.fullmatch(r"[a-f0-9]{64}", str(selected.get(key, "")))
                           for key in ("contentId", "recordingId", "sha256", "receiptSha256"))
                    or not isinstance(selected.get("failedChecks"), list)
                    or not selected["failedChecks"]
                    or any(not isinstance(value, str) or not value for value in selected["failedChecks"])):
                return False
            if per_voice:
                voice = selected.get("voiceId")
                if (not isinstance(voice, str) or voice not in voice_versions
                        or selected.get("voiceVersion") != voice_versions[voice]):
                    return False
                identity = (selected["contentId"], voice)
                voice_counts[voice] += 1
                if voice_counts[voice] > release["contents"]:
                    return False
            else:
                if (selected.get("voiceId", "mandarin-male") != "mandarin-male"
                        or selected.get("voiceVersion", profiles["male"]) != profiles["male"]):
                    return False
                identity = selected["contentId"]
            if identity in identities:
                return False
            identities.add(identity)
        if per_voice:
            for approval in approvals:
                voice = approval.get("voiceId")
                if (voice not in voice_versions
                        or approval.get("voiceVersion") != voice_versions[voice]):
                    return False
                identity = (approval.get("contentId"), voice)
                if identity in identities:
                    return False
                identities.add(identity)
                voice_counts[voice] += 1
                if voice_counts[voice] > release["contents"]:
                    return False
            if len({identity[0] for identity in identities}) > release["contents"]:
                return False
    return (
        (owner_count > 0 or processed_count > 0 or release.get("qualified") is True)
        and len({approval["approvalId"] for approval in approvals}) == len(approvals)
        and release.get("humanApprovedRecordings") == len(approvals)
        and type(release.get("automatedRecordings")) is int
        and release["automatedRecordings"] >= 0
        and release.get("recordings") == release["automatedRecordings"] + len(approvals) + owner_count + processed_count
        and release["recordings"] == release.get("contents", 0) * 2
        and all(
            approval["voiceVersion"] in release.get("profiles", {}).values()
            for approval in approvals
        )
    )


def release_is_qualified(release):
    """Availability includes the exact owner-approved flagged catalog."""
    if release.get('reviewPolicy'):
        from app.services.audio_catalog_closure import closure_release_available
        path = Path(__file__).resolve().parents[1] / 'data/sentence-audio/catalog-closure-approvals.json'
        approvals = json.loads(path.read_text()) if path.is_file() else {}
        return closure_release_available(release, approvals.get(release.get('releaseId')))
    return _legacy_release_is_qualified(release)
