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
