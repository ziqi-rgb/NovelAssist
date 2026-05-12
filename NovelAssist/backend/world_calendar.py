"""时钟纪元引擎 — Tick ↔ 日期 双向转换"""


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
