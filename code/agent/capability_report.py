"""Validate bounded observations from the connected host before exposing them to an LLM."""
from __future__ import annotations


def normalize_capabilities(value):
    if not isinstance(value, dict) or type(value.get('schemaVersion')) is not int or value['schemaVersion'] != 1:
        return None
    host = value.get('host')
    if not isinstance(host, dict) or host.get('packageName') != 'com.autoprocedure.plat':
        return None
    result = {'schemaVersion': 1, 'host': {'packageName': host['packageName']}}
    for key in ('versionCode', 'sdk'):
        number = host.get(key)
        if type(number) is int and 0 < number < 100000:
            result['host'][key] = number
    result['host']['versionName'] = str(host.get('versionName', ''))[:40]
    for key in ('audioOutput', 'offlineEnglishTts'):
        source = value.get(key)
        if not isinstance(source, dict):
            source = {}
        section = {'status': source.get('status') if source.get('status') in
                   ('supported', 'unavailable', 'unknown') else 'unknown',
                   'audibility': 'not_tested'}
        for field in ('reasonCode', 'scope', 'engine', 'voice', 'locale'):
            if isinstance(source.get(field), str):
                section[field] = source[field][:200]
        for field in ('mediaVolume', 'mediaMaxVolume', 'synthesizedBytes'):
            if type(source.get(field)) is int and 0 <= source[field] <= 10_000_000:
                section[field] = source[field]
        for field in ('muted', 'networkRequired'):
            if type(source.get(field)) is bool:
                section[field] = source[field]
        if key == 'offlineEnglishTts' and section['status'] == 'supported':
            if not (section.get('reasonCode') == 'OFFLINE_SYNTHESIS_VERIFIED'
                    and section.get('networkRequired') is False
                    and section.get('locale', '').split('-')[0] == 'en'
                    and section.get('synthesizedBytes', 0) > 44
                    and section.get('engine') and section.get('voice')):
                section.update(status='unknown', reasonCode='INCOMPLETE_SYNTHESIS_EVIDENCE')
        result[key] = section
    return result
