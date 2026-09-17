from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from .client import Client
from .errors import InvalidInput, UpstreamError
from .transport import AuthRequired, ISMUError, private_write


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="IS MU access without a browser. Read-only collection plus explicit ROPOT actions."
    )
    parser.add_argument("--state-dir", default=os.environ.get("IS_MU_STATE_DIR", ".ismu-state"))
    parser.add_argument("--study", type=int)
    parser.add_argument("--period", type=int)
    parser.add_argument("--faculty", type=int)
    sub = parser.add_subparsers(dest="command", required=True)
    login = sub.add_parser(
        "login", help="Normal IS MU login; password is prompted and never stored"
    )
    login.add_argument("--username", required=True)
    sub.add_parser("status", help="Verify saved session against the live course page")
    courses = sub.add_parser(
        "courses", help="Discover live enrolments, groups and read-source URLs"
    )
    courses.add_argument("--table", action="store_true")
    courses.add_argument("--basic", action="store_true")
    for name in ["files", "syllabus", "ropots", "forums", "reservations"]:
        cmd = sub.add_parser(name)
        cmd.add_argument("course")
    sub.add_parser("notes")
    submissions = sub.add_parser("submissions", help="Open boxes, or visible boxes in one course")
    submissions.add_argument("course", nargs="?")
    box = sub.add_parser("submission-box", help="Permissions and visible files in one ode/ folder")
    box.add_argument("course")
    box.add_argument("path", help="Course-relative ode/.../ directory")
    calendar = sub.add_parser("calendar", help="Native calendar events; date end is exclusive")
    calendar.add_argument("--start")
    calendar.add_argument("--end")
    calendar.add_argument("--kind", action="append", dest="kinds")
    sub.add_parser("timetable", help="Weekly pattern and source exception text")
    exams = sub.add_parser("exams", help="Exam/registration series; no registrations are changed")
    exams.add_argument("--max-series", type=int, default=30)
    notices = sub.add_parser("notices", help="Noticeboard cards without opening their messages")
    notices.add_argument("--board", help="IS board or board/section selector")
    notices.add_argument("--max-pages", type=int, default=1)
    notice = sub.add_parser("notice", help="Open one notice, which may mark it read upstream")
    notice.add_argument("id")
    notice.add_argument("--allow-mark-read", action="store_true")
    mail = sub.add_parser("mail", help="Mailbox reads through the existing HTTPS session")
    mail_sub = mail.add_subparsers(dest="mail_action", required=True)
    mail_sub.add_parser("folders")
    mail_list = mail_sub.add_parser("list")
    mail_list.add_argument("--folder")
    mail_list.add_argument("--start", type=int, default=1)
    mail_list.add_argument("--limit", type=int, default=50)
    for name in ("read", "download"):
        action = mail_sub.add_parser(name)
        action.add_argument("id")
        action.add_argument("--folder", required=True)
        action.add_argument(
            "--allow-mark-read",
            action="store_true",
            help="Permit native mail export to mark the message read",
        )
        if name == "download":
            action.add_argument("--output", required=True)
            action.add_argument(
                "--part", help="MIME attachment part_id; omit for the complete mail export"
            )
    read = sub.add_parser("read", help="Read a course-relative text file or download a binary")
    read.add_argument("course")
    read.add_argument("path")
    read.add_argument("--output")
    snapshot = sub.add_parser(
        "snapshot", help="Collect course resources with per-resource completeness states"
    )
    snapshot.add_argument("--output")
    snapshot.add_argument("--skip-files", action="store_true")
    serve = sub.add_parser("serve", help="Serve authenticated, read-only JSON on localhost")
    serve.add_argument("--port", type=int, default=8765)
    ropot = sub.add_parser("ropot", help="Explicit ROPOT entry, questions and native form actions")
    ropot.add_argument("action", choices=["inspect", "start", "questions", "save", "submit"])
    ropot.add_argument("qref", help="Plain /el/... or /do/... .qref path")
    ropot.add_argument(
        "--public", action="store_true", help="Separate anonymous cookie jar for public demos"
    )
    ropot.add_argument(
        "--allow-attempt",
        action="store_true",
        help="Enable a start/save action that changes attempt state",
    )
    ropot.add_argument(
        "--answers", help="Private JSON object keyed by discovered answer-field names"
    )
    ropot.add_argument(
        "--confirm-submit", action="store_true", help="Explicitly submit the reviewed attempt once"
    )
    args = parser.parse_args(argv)
    try:
        c = Client(args.state_dir, study=args.study, period=args.period, faculty=args.faculty)
        if args.command == "ropot":
            from .ropot import RopotSession

            attempt = RopotSession(
                args.state_dir,
                args.qref,
                public=args.public,
                allow_writes=args.allow_attempt or args.confirm_submit,
            )
            answers = json.loads(Path(args.answers).read_text()) if args.answers else {}
            if args.action == "submit":
                result = attempt.submit(answers, confirm=args.confirm_submit)
            elif args.action == "save":
                if not args.answers:
                    raise InvalidInput("Save requires --answers with a private JSON answer file.")
                result = attempt.save(answers)
            else:
                result = getattr(attempt, args.action)()
        elif args.command == "login":
            password = getpass.getpass("IS MU primary password: ")
            try:
                result = c.login(args.username, password)
            finally:
                password = None
        elif args.command == "serve":
            from .server import serve

            serve(c, args.port)
            return 0
        elif args.command == "status":
            result = c.status()
        elif args.command == "courses":
            result = c.courses(details=not args.basic)
            if args.table:
                for row in result:
                    print(
                        f"{row['code']:<9} {str(row.get('credits', '')):>2} cr  {row.get('seminar') or '—':<10} {row['title']}"
                    )
                return 0
        elif args.command == "read":
            result = c.read_file(args.course, args.path, destination=args.output)
        elif args.command == "snapshot":
            result = c.snapshot(include_files=not args.skip_files)
            if args.output:
                private_write(
                    Path(args.output), json.dumps(result, ensure_ascii=False, indent=2).encode()
                )
                result = {
                    "saved_to": str(Path(args.output).resolve()),
                    "course_count": len(result["courses"]),
                    "complete": result["complete"],
                    "errors": result["errors"],
                }
        elif args.command == "notes":
            result = c.notes()
        elif args.command == "submissions":
            result = c.submissions(args.course) if args.course else c.open_submissions()
        elif args.command == "submission-box":
            result = c.submission_box(args.course, args.path)
        elif args.command == "calendar":
            result = c.calendar(start=args.start, end=args.end, kinds=args.kinds)
        elif args.command == "timetable":
            result = c.timetable()
        elif args.command == "exams":
            result = c.exams(max_series=args.max_series)
        elif args.command == "notices":
            result = c.notices(board=args.board, max_pages=args.max_pages)
        elif args.command == "notice":
            result = c.notice(args.id, allow_mark_read=args.allow_mark_read)
        elif args.command == "mail":
            if args.mail_action == "folders":
                result = c.mail_folders()
            elif args.mail_action == "list":
                result = c.mail_messages(folder=args.folder, start=args.start, limit=args.limit)
            elif args.mail_action == "read":
                result = c.mail_message(
                    args.id, folder=args.folder, allow_mark_read=args.allow_mark_read
                )
            else:
                result = c.download_mail(
                    args.id,
                    folder=args.folder,
                    destination=args.output,
                    part_id=args.part,
                    allow_mark_read=args.allow_mark_read,
                )
        else:
            result = getattr(c, args.command)(args.course)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if isinstance(result, dict) and not result.get("complete", True) else 0
    except (ISMUError, OSError, ValueError) as exc:
        error = {"error": type(exc).__name__, "message": str(exc)}
        if isinstance(exc, ISMUError):
            error["code"] = exc.code
        if isinstance(exc, UpstreamError) and exc.status_code is not None:
            error["upstream_status"] = exc.status_code
        print(
            json.dumps(error, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2 if isinstance(exc, AuthRequired) else 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
