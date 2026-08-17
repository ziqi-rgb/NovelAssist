"""时钟纪元引擎 — Tick ↔ 日期 双向转换"""

import re
from typing import Tuple, List, Dict


class CalendarEngine:
    @staticmethod
    def tick_to_datetime(tick: int, config: dict) -> dict:
        hpd = config.get("hours_per_day", 24)
        dpm = config.get("days_per_month", 30)
        mpy = config.get("months_per_year", 12)

        hours = tick % hpd
        days_total = tick // hpd
        days = (days_total % dpm) + 1
        months_total = days_total // dpm
        months = (months_total % mpy) + 1
        years = (months_total // mpy) + 1

        return {"year": years, "month": months, "day": days, "hour": hours}

    @staticmethod
    def datetime_to_tick(year: int, month: int, day: int, hour: int, config: dict) -> int:
        hpd = config.get("hours_per_day", 24)
        dpm = config.get("days_per_month", 30)
        mpy = config.get("months_per_year", 12)

        tick = (year - 1) * mpy * dpm * hpd
        tick += (month - 1) * dpm * hpd
        tick += (day - 1) * hpd
        tick += hour
        return tick

    @staticmethod
    def is_routine_active(tick: int, config: dict, cycle_type: str, cycle_value: str) -> bool:
        dt = CalendarEngine.tick_to_datetime(tick, config)
        val = str(cycle_value).strip()
        if not val or val == "常驻":
            return False
        try:
            if cycle_type == "日":
                h = int(val.split(":")[0])
                return dt["hour"] == h
            elif cycle_type == "月":
                d = int(val.split(" ")[0])
                return dt["day"] == d
            elif cycle_type == "年":
                parts = val.split(" ")[0].split("-")
                return dt["month"] == int(parts[0]) and dt["day"] == int(parts[1])
        except Exception:
            pass
        return False

    @staticmethod
    def format_tick_to_lore_time(tick: int, config: dict) -> str:
        dt = CalendarEngine.tick_to_datetime(tick, config)

        y_str = f"{dt['year']}年"
        year_names = config.get("year_names") or []
        if isinstance(year_names, str):
            year_names = [n.strip() for n in year_names.split(",") if n.strip()]
        if year_names:
            y_str = f"{year_names[(dt['year'] - 1) % len(year_names)]}年"

        era = config.get("era_name", "").strip()
        if era:
            y_str = f"{era} {y_str}"

        m_str = f"{dt['month']}月"
        month_names = config.get("month_names") or []
        if isinstance(month_names, str):
            month_names = [n.strip() for n in month_names.split(",") if n.strip()]
        if month_names and len(month_names) >= dt["month"]:
            m_str = month_names[dt["month"] - 1]

        d_str = f"{dt['day']}日"
        day_names = config.get("day_names") or []
        if isinstance(day_names, str):
            day_names = [n.strip() for n in day_names.split(",") if n.strip()]
        if day_names and len(day_names) >= dt["day"]:
            d_str = day_names[dt["day"] - 1]

        h_str = f"{dt['hour']}时"
        hour_names = config.get("hour_names") or []
        if isinstance(hour_names, str):
            hour_names = [n.strip() for n in hour_names.split(",") if n.strip()]
        if hour_names and len(hour_names) > dt["hour"]:
            h_str = hour_names[dt["hour"]]

        return f"{y_str} {m_str} {d_str} {h_str}"

    # ── Time Marker Parser ──
    # Supports markers like:
    #   【时间流逝：3天】
    #   【时间流逝：1年2月】
    #   【时间流逝：半个时辰】
    #   【时间流逝：三年后】
    # Units: 年, 月/个月, 天/日, 小时, 时辰/个时辰 (1时辰=2小时)
    # Special: 半 = 0.5 (e.g. 半个月=15days, 半个时辰=1hour)

    _CN_NUM_MAP: dict = {
        '零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
        '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
        '百': 100, '千': 1000, '万': 10000, '亿': 100000000,
    }

    @staticmethod
    def _parse_chinese_num(s: str) -> int:
        """Parse a Chinese numeral or integer string to int.
        Handles: 半 → returns -1 as sentinel (caller deals with 0.5).
        """
        s = s.strip()
        if s == '半':
            return -1  # sentinel
        try:
            return int(s)
        except ValueError:
            pass
        result = 0
        current = 0
        for ch in s:
            val = CalendarEngine._CN_NUM_MAP.get(ch)
            if val is None:
                continue
            if val >= 10:
                if current == 0:
                    current = 1
                result += current * val
                current = 0
            else:
                current = val
        result += current
        return result if result > 0 else 1  # bare unit implies 1

    @staticmethod
    def parse_time_markers(text: str, config: dict = None) -> Tuple[int, list]:
        """Parse 【时间流逝：...】 markers from body text.

        Returns (total_tick_increment, [marker_info, ...]) where each
        marker_info is a dict with keys: text, start, end, ticks, description.
        """
        if config is None:
            config = {}
        hpd = config.get("hours_per_day", 24)
        dpm = config.get("days_per_month", 30)
        mpy = config.get("months_per_year", 12)

        ticks_per_hour = 1
        ticks_per_day = hpd
        ticks_per_month = dpm * hpd
        ticks_per_year = mpy * dpm * hpd

        # Match the marker body between 【时间流逝：/ : and 】
        pattern = r'【时间流逝[：:]([^】]+)】'
        total_ticks = 0
        markers: list = []

        for m in re.finditer(pattern, text):
            time_expr = m.group(1).strip()
            # Strip trailing 后 / 之后
            time_expr = re.sub(r'[之后]$', '', time_expr)

            marker_ticks = 0
            desc_parts: list = []

            # Parse (amount)(unit) pairs
            comp_re = r'(半|[一二三四五六七八九十百千万亿\d]+)\s*(年|个月|月|天|日|小时|时辰|个时辰)'
            for cm in re.finditer(comp_re, time_expr):
                amt_str = cm.group(1)
                unit = cm.group(2)

                raw = CalendarEngine._parse_chinese_num(amt_str)
                if raw == -1:  # 半
                    amt = 0.5
                else:
                    amt = float(raw)

                if unit == '年':
                    marker_ticks += int(amt * ticks_per_year)
                    desc_parts.append(f"{amt_str}年")
                elif unit in ('月', '个月'):
                    marker_ticks += int(amt * ticks_per_month)
                    desc_parts.append(f"{amt_str}月")
                elif unit in ('天', '日'):
                    marker_ticks += int(amt * ticks_per_day)
                    desc_parts.append(f"{amt_str}天")
                elif unit == '小时':
                    marker_ticks += int(amt * ticks_per_hour)
                    desc_parts.append(f"{amt_str}小时")
                elif unit in ('时辰', '个时辰'):
                    marker_ticks += int(amt * 2)  # 1时辰=2小时
                    desc_parts.append(f"{amt_str}时辰")

            if marker_ticks > 0:
                total_ticks += marker_ticks
                markers.append({
                    'text': m.group(0),
                    'start': m.start(),
                    'end': m.end(),
                    'ticks': marker_ticks,
                    'description': ' '.join(desc_parts),
                })

        return total_ticks, markers

    @staticmethod
    def strip_time_markers(text: str) -> str:
        """Remove all 【时间流逝：...】 markers from text.
        Returns the cleaned text with markers stripped out.
        Also cleans up any double-newlines that may result from removal.
        """
        cleaned = re.sub(r'【时间流逝[：:][^】]+】\s*', '', text)
        # Collapse 3+ consecutive newlines into 2
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        return cleaned.strip()
