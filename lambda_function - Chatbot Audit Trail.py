import json
import boto3
import os
import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3  = boto3.client('s3',  region_name=os.environ['REGION'])
ses = boto3.client('ses', region_name=os.environ['REGION'])

BUCKET    = os.environ['CHAT_LOG_BUCKET']
RECIPIENT = os.environ['RECIPIENT_EMAIL']
SENDER    = os.environ['SENDER_EMAIL']


def list_all_keys(bucket: str) -> list:
    keys = []
    paginator = s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get('Contents', []):
            keys.append(obj['Key'])
    return keys


def load_records(bucket: str, keys: list) -> list:
    required = {'session_id', 'timestamp', 'question', 'answer'}
    records = []
    for key in keys:
        try:
            response = s3.get_object(Bucket=bucket, Key=key)
            record = json.loads(response['Body'].read())
            if not required.issubset(record.keys()):
                logger.warning(f"Skipping malformed record {key}: missing fields")
                continue
            records.append(record)
        except Exception as e:
            logger.error(f"Failed to read {key}: {e}")
    return records


def group_by_session(records: list) -> dict:
    sessions = defaultdict(list)
    for r in records:
        sessions[r['session_id']].append(r)
    for sid in sessions:
        sessions[sid].sort(key=lambda x: x['timestamp'])
    return dict(sessions)


def format_email(sessions: dict, start_date: str, end_date: str) -> str:
    total_exchanges = sum(len(v) for v in sessions.values())
    lines = [
        f"Period: {start_date} - {end_date}",
        f"Total conversations: {len(sessions)} | Total exchanges: {total_exchanges}",
        ""
    ]
    sorted_sessions = sorted(sessions.items(), key=lambda x: x[1][0]['timestamp'])
    for sid, exchanges in sorted_sessions:
        try:
            first_ts = datetime.fromisoformat(exchanges[0]['timestamp'].replace('Z', '+00:00'))
            session_label = first_ts.strftime('%Y-%m-%d at %H:%M UTC')
        except (ValueError, KeyError):
            session_label = 'unknown time'
        lines.append("=" * 40)
        lines.append(f"Session: {session_label}")
        lines.append("=" * 40)
        for exchange in exchanges:
            lines.append(f"Q: {exchange['question']}")
            lines.append(f"A: {exchange['answer']}")
            lines.append("")
    return "\n".join(lines)


def handler(event, context):
    today = datetime.now(timezone.utc)

    keys    = list_all_keys(BUCKET)
    records = load_records(BUCKET, keys)

    end_str   = today.strftime('%Y-%m-%d')
    start_str = (today - timedelta(days=7)).strftime('%Y-%m-%d')
    subject   = f"PolyBot Weekly Chat Digest — {end_str}"

    if not records:
        body = f"Period: {start_str} - {end_str}\n\nNo conversations this week."
    else:
        sessions = group_by_session(records)
        body = format_email(sessions, start_str, end_str)

    try:
        ses.send_email(
            Source=SENDER,
            Destination={'ToAddresses': [RECIPIENT]},
            Message={
                'Subject': {'Data': subject},
                'Body':    {'Text': {'Data': body}}
            }
        )
        logger.info(f"Digest sent: {len(records)} exchanges from {len(keys)} log files")
    except Exception as e:
        logger.error(f"Failed to send digest email: {e}")
        return {'statusCode': 500}
    return {'statusCode': 200}
