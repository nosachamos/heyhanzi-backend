"""Exercise real release learning/auth traffic and migrations within 768 MiB."""
import argparse
import base64
import json
import os
import secrets
import subprocess
import time
import urllib.request
import uuid
from http.cookies import SimpleCookie
from pathlib import Path
from tempfile import TemporaryDirectory

MEMORY_MIB = 768


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True, **kwargs)


def memory_snapshot(container):
    """Read the actual API PID and kernel cgroup peaks, not the client process."""
    script = '''import json
from pathlib import Path
fields = {}
for line in Path('/proc/1/status').read_text().splitlines():
    if line.startswith(('VmRSS:', 'VmHWM:')):
        key, value = line.split(':', 1)
        fields[key + 'Bytes'] = int(value.split()[0]) * 1024
group = Path('/sys/fs/cgroup')
for name in ['memory.current', 'memory.peak', 'memory.max']:
    path = group / name
    if path.exists():
        value = path.read_text().strip()
        fields[name] = int(value) if value.isdigit() else value
events = group / 'memory.events'
if events.exists():
    fields['events'] = {line.split()[0]: int(line.split()[1]) for line in events.read_text().splitlines()}
print(json.dumps(fields))'''
    return json.loads(docker('exec', container, 'python', '-c', script).stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument('--report', type=Path, default=Path('.deployment-reports/runtime-memory.json'))
    args = parser.parse_args()
    identifier = "heyhanzi-check-" + uuid.uuid4().hex[:10]
    network, database, api = identifier, identifier + "-db", identifier + "-api"
    provider = identifier + "-turnstile"
    fixture_directory = TemporaryDirectory(prefix="heyhanzi-turnstile-rehearsal-")
    fixture = Path(fixture_directory.name)
    config = {
        "GENERAL": {"Environment": "PROD", "ReleaseSha": "memory-check"},
        "URLS": {"FrontendUrl": "https://heyhanzi.com", "BackendUrl": "https://api.heyhanzi.com"},
        "AUTH": {name: base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
                 for name in ("SecretKey", "AuditHashSecret", "TokenEncryptionKey")},
        "EMAIL": {"Provider": "ses", "FromEmail": "no-reply@heyhanzi.com", "FromName": "HeyHanzi",
                  "SupportUrl": "https://heyhanzi.com", "SesRegion": "us-east-1"},
        "AWS": {"AccessKeyId": "nonfunctional-benchmark", "SecretAccessKey": "nonfunctional-benchmark"},
        "TURNSTILE": {"SiteKey": "benchmark-production-site", "SecretKey": secrets.token_urlsafe(32),
                      "Hostnames": ["heyhanzi.com"]},
        # Local disposable Postgres in this isolated network uses TLS below.
        "DATABASE": {"Url": "postgresql://postgres:postgres@db:5432/heyhanzi_test?sslmode=require"},
    }
    process_env = {**os.environ, "APP_CONFIGS": json.dumps(config)}
    memory_limit = f'{MEMORY_MIB}m'
    benchmark_password = secrets.token_urlsafe(24)
    fixture_env = {**process_env, 'HEYHANZI_MEMORY_FIXTURE_PASSWORD': benchmark_password}
    try:
        docker("network", "create", network)
        # Substitute only Cloudflare's external TLS service, never application enforcement.
        # This private CA and fixture are mounted into rehearsal containers, not the image.
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(fixture / "key.pem"), "-out", str(fixture / "cert.pem"),
            "-subj", "/CN=challenges.cloudflare.com", "-days", "1",
            "-addext", "subjectAltName=DNS:challenges.cloudflare.com"],
            check=True, capture_output=True)
        (fixture / "secret").write_text(config["TURNSTILE"]["SecretKey"])
        (fixture / "provider.py").write_text(Path("tests/fixtures/turnstile_siteverify.py").read_text())
        docker("run", "-d", "--name", provider, "--network", network,
            "--network-alias", "challenges.cloudflare.com", "--user", "0",
            "-v", f"{fixture}:/fixture:ro", args.image, "python", "/fixture/provider.py")
        ca_path = docker("run", "--rm", args.image, "python", "-c",
                         "import certifi; print(certifi.where())").stdout.strip()
        # Generate TLS inside the disposable database container. No host CA changes.
        docker(
            "run", "-d", "--name", database, "--network", network, "--network-alias", "db",
            "--tmpfs", "/var/lib/postgresql/data:rw,size=2g",
            "-e", "POSTGRES_PASSWORD=postgres", "-e", "POSTGRES_DB=heyhanzi_test",
            "postgres:16-bookworm", "bash", "-ec",
            "openssl req -x509 -newkey rsa:2048 -nodes -keyout /tmp/server.key "
            "-out /tmp/server.crt -subj /CN=db -days 1 >/dev/null 2>&1; "
            "chown postgres:postgres /tmp/server.key; chmod 600 /tmp/server.key; "
            "exec docker-entrypoint.sh postgres -c ssl=on "
            "-c ssl_cert_file=/tmp/server.crt -c ssl_key_file=/tmp/server.key",
        )
        for _ in range(60):
            check = subprocess.run(
                ["docker", "exec", database, "pg_isready", "-U", "postgres", "-d", "heyhanzi_test"],
                capture_output=True,
            )
            if check.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Disposable Postgres did not start")
        for _ in range(2):
            docker(
                "run", "--rm", "--network", network, f"--memory={memory_limit}", f"--memory-swap={memory_limit}",
                "-e", "APP_CONFIGS", "-e", "APP_ENV=PROD", args.image,
                "python", "scripts/migrate_release.py", env=process_env,
            )
        # Test-only fixture writes target this freshly created private database.
        # This process exits before the API starts, so it cannot hide/double-count
        # the API's own catalog load within the same memory budget.
        prepared = json.loads(docker('run', '--rm', '--network', network,
            f'--memory={memory_limit}', f'--memory-swap={memory_limit}',
            '-e', 'APP_CONFIGS', '-e', 'APP_ENV=PROD', '-e', 'HEYHANZI_MEMORY_FIXTURE_PASSWORD',
            args.image, 'python', 'scripts/prepare_release_memory_fixture.py', env=fixture_env).stdout)
        docker(
            "run", "-d", "--name", api, "--network", network,
            f"--memory={memory_limit}", f"--memory-swap={memory_limit}", "--cpus=1",
            "-p", "127.0.0.1::8000", "-e", "APP_CONFIGS", "-e", "APP_ENV=PROD",
            "-v", f"{fixture / 'cert.pem'}:{ca_path}:ro",
            args.image, env=process_env,
        )
        port = docker("port", api, "8000/tcp").stdout.strip().rsplit(":", 1)[1]
        base = f"http://127.0.0.1:{port}"
        for _ in range(60):
            try:
                with urllib.request.urlopen(base + "/ready", timeout=5) as response:
                    if response.status == 200:
                        break
            except (OSError, TimeoutError):
                time.sleep(1)
        else:
            raise RuntimeError("Release image did not become ready")
        snapshots = {'readyBeforeCourseLoad': memory_snapshot(api)}
        for path in ["/health", "/ready"] * 20:
            with urllib.request.urlopen(base + path, timeout=5) as response:
                if response.status != 200:
                    raise RuntimeError("Release health check failed")
        # Unknown-user login still performs a production-cost Argon2 verification.
        # It never queues email or makes an SES call.
        from urllib.error import HTTPError
        for attempt in range(4):
            payload = {"email": "memory-check@example.com", "password": "invalid-password"}
            if attempt:
                payload["turnstileToken"] = f"memory-check-{attempt}"
            request = urllib.request.Request(base + "/auth/email/login", method="POST",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "Origin": "https://heyhanzi.com"})
            try:
                urllib.request.urlopen(request, timeout=10)
            except HTTPError as error:
                if error.code != (401 if attempt else 403):
                    raise
            else:
                raise RuntimeError("Unknown-user authentication unexpectedly succeeded")
        # Authenticate a real verified fixture account. Secure cookies are
        # explicitly forwarded only over this isolated loopback rehearsal hop,
        # matching the private HTTP hop behind production TLS termination.
        login = urllib.request.Request(base+'/auth/email/login', method='POST',
            data=json.dumps({'email': prepared['email'], 'password': benchmark_password,
                             'turnstileToken': 'memory-check-valid-login'}).encode(),
            headers={'Content-Type': 'application/json', 'Origin': 'https://heyhanzi.com'})
        with urllib.request.urlopen(login, timeout=20) as response:
            if response.status != 200:
                raise RuntimeError('Rehearsal learner authentication failed')
            cookies = SimpleCookie()
            for value in response.headers.get_all('Set-Cookie', []):
                cookies.load(value)
            session_headers = {'Cookie': '; '.join(f'{key}={value.value}' for key,value in cookies.items()),
                               'X-CSRF-Token': response.headers['X-CSRF-Token']}
            response.read()

        def course_request(path, payload=None):
            headers = {**session_headers, 'Origin': 'https://heyhanzi.com'}
            if payload is not None:
                headers['Content-Type'] = 'application/json'
            request = urllib.request.Request(base+path, method='GET' if payload is None else 'POST',
                data=None if payload is None else json.dumps(payload).encode(), headers=headers)
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status != 200 or response.headers.get('Cache-Control') != 'no-store':
                    raise RuntimeError('Rehearsal course request did not return uncached success')
                return json.load(response)

        course_state = course_request('/learning/course')
        if any(item['id'] not in course_state['eligible'] for item in prepared['targets']):
            raise RuntimeError('Rehearsal fixture did not satisfy real course prerequisites')
        snapshots['afterColdAccountCourseState'] = memory_snapshot(api)

        # Required stories retain their actual earlier-story gate. Obtain real
        # signed guest results through public checks, then import those results
        # into this disposable account rather than forging durable completion.
        guest_progress = {'runs': []}

        def guest_request(operation, **fields):
            return course_request('/learning/course/guest', {'operation': operation,
                'progress': guest_progress, 'notebook': prepared['notebook'], **fields})

        for instance_id in prepared['requiredPrefix']:
            exercise = guest_request('exercise', instanceId=instance_id)['exercise']
            run_id = str(uuid.uuid4())
            saved = guest_request('save', runId=run_id, save={
                'instanceId': instance_id, 'revision': exercise['revision'],
                'fingerprint': exercise['fingerprint'], 'prepared': True,
                'draft': [], 'supports': []})
            guest_progress = saved['progress']
            exercise = guest_request('exercise', instanceId=instance_id)['exercise']

            def check_part(answer, supports=()):
                nonlocal guest_progress
                result = guest_request('check', runId=run_id, check={
                    'attemptId': str(uuid.uuid4()), 'revision': exercise['revision'],
                    'fingerprint': exercise['fingerprint'], 'answer': answer, 'supports': list(supports)})
                guest_progress = result['progress']
                return result

            for step in exercise['definition']['steps']:
                if exercise['definition'].get('setActivity'):
                    prefix = ['set-v1', step['id']]
                    if step['kind'] == 'stroke':
                        check_part([*prefix, 'stroke', 'paper', json.dumps({'evidence':'paper','corrections':0})])
                    for question in step.get('questions', []):
                        supports = ['audio-heard'] if question.get('audio') or question.get('listeningRequired') else []
                        if question['kind'] == 'choice':
                            mode, value = 'choice', next(c['id'] for c in question['choices'] if c['correct'])
                        elif question['kind'] == 'production':
                            supports += ['review:' + row['id'] + ':retry' for row in question['rubric']]
                            if question['response'] == 'spoken':
                                mode, value = 'unrecorded', ''
                                supports.append('practiced-aloud')
                            else:
                                mode, value = 'hanzi', question.get('model', {}).get('text') or '我' * question.get('minLength', 1)
                        else:
                            answer = question['accepted'][0]
                            mode, value = answer.get('mode', 'hanzi'), answer['value']
                        check_part([*prefix, question['id'], mode, value], supports)
                    checked = check_part([*prefix, 'advance'])
                    continue
                if step['kind'] != 'check':
                    continue
                question = step['questions'][0]
                answer = ([question['id'], next(choice['id'] for choice in question['choices']
                                                if choice['correct'])]
                          if question['kind'] == 'choice' else question['solutions'][0])
                checked = check_part(answer)
            if not checked['run']['completed']:
                raise RuntimeError('Rehearsal required story did not complete through public checks')
        expected_completed = set(prepared['requiredPrefix'])
        if expected_completed:
            account = course_request('/auth/session')
            imported = course_request('/learning/course/import', {
                'accountId': account['id'], 'progress': guest_progress})
            if set(imported['completed']) != expected_completed:
                raise RuntimeError('Rehearsal signed prerequisite import changed completion')
        snapshots['afterPrerequisiteAssessment'] = memory_snapshot(api)

        def verify_detail(detail, target):
            exercise = detail['exercise']
            if exercise['fingerprint'] != target['fingerprint']:
                raise RuntimeError('Rehearsal course detail used a different catalog')
            actual = {
                pid: {voice: {'kind': 'clips', 'count': len(value['clips'])}
                      if 'clips' in value else {'kind': 'whole'}
                      for voice, value in variants.items()}
                for pid, variants in exercise['definition'].get('storyActivity', {})
                .get('recordings', {}).get('paragraphs', {}).items()}
            if actual != target['paragraphDelivery']:
                raise RuntimeError('Rehearsal course detail changed paragraph sentence delivery')

        for target in prepared['targets']:
            detail = course_request('/learning/course/exercises/'+target['id'])
            verify_detail(detail, target)
        snapshots['afterAccountDetails'] = memory_snapshot(api)
        for _ in range(3):
            guest = course_request('/learning/course/guest', {'operation': 'state',
                'progress': guest_progress, 'notebook': prepared['notebook']})
            if set(guest['completed']) != expected_completed:
                raise RuntimeError('Rehearsal guest acquired invented completion')
            for target in prepared['targets']:
                detail = course_request('/learning/course/guest', {'operation': 'exercise',
                    'progress': guest['progress'], 'notebook': prepared['notebook'], 'instanceId': target['id']})
                verify_detail(detail, target)
            course_request('/learning/course')
        snapshots['steadyAfterRepeatedCourseRequests'] = memory_snapshot(api)
        print(docker("stats", "--no-stream", "--format", "{{.MemUsage}}", api).stdout.strip())
        state = json.loads(docker("inspect", "--format", "{{json .State}}", api).stdout)
        if state["OOMKilled"] or not state["Running"]:
            raise RuntimeError("Release image exceeded its budget or exited")
        if any(snapshot.get('events', {}).get('oom_kill', 0) for snapshot in snapshots.values()):
            raise RuntimeError('Release course traffic triggered a cgroup OOM kill')
        if any(snapshot.get('memory.max') != MEMORY_MIB * 1024 * 1024
               or not isinstance(snapshot.get('memory.peak'), int)
               for snapshot in snapshots.values()):
            raise RuntimeError('Release rehearsal requires the recorded hard memory limit and kernel peak')
        report = {'memoryLimitMiB': MEMORY_MIB, 'apiWorkers': 1, 'cpus': 1,
            'catalogSha256': prepared['catalogSha256'], 'catalogBytes': prepared['catalogBytes'],
            'exerciseCount': prepared['exerciseCount'], 'targets': prepared['targets'],
            'fixtureProcessPeakRssKiB': prepared['fixturePeakRssKiB'], 'apiMemory': snapshots,
            'imageId': docker('image', 'inspect', args.image, '--format', '{{.Id}}').stdout.strip(),
            'checks': ['repeated-real-migrations', 'liveness/readiness', 'production-cost-password-checks',
                       'authenticated-course-state', 'cloze-and-large-optional-story-detail',
                       'mixed-whole-and-sentence-story-delivery',
                       *(['required-prefix-public-checks-and-signed-import'] if expected_completed else []),
                       'guest-course-state-and-detail', 'repeated-course-requests'],
            'running': True, 'oomKilled': False, 'productionDeployed': False}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report, indent=2))
        print(f"{MEMORY_MIB} MiB / 1 CPU / 1 worker release learning/auth and repeated migrations passed.")
    except Exception as error:
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr[-4000:])
        logs = subprocess.run(['docker', 'logs', '--tail', '30', api], capture_output=True, text=True)
        if logs.returncode == 0:
            print(logs.stdout)
            print(logs.stderr)
        raise
    finally:
        for container in (api, database, provider):
            subprocess.run(["docker", "rm", "-f", "-v", container], capture_output=True)
        subprocess.run(["docker", "network", "rm", network], capture_output=True)
        fixture_directory.cleanup()


if __name__ == "__main__":
    main()
