"use strict";

const habitFormat = (() => {
  const zone = window.habitConfig.timezone;
  const dayFormat = new Intl.DateTimeFormat("en-SG", {timeZone:"UTC",day:"numeric",month:"short",year:"numeric"});
  const weekdayFormat = new Intl.DateTimeFormat("en-SG", {timeZone:"UTC",weekday:"long",day:"numeric",month:"short",year:"numeric"});
  const timeFormat = new Intl.DateTimeFormat("en-SG", {timeZone:zone,hour:"2-digit",minute:"2-digit",hourCycle:"h23"});
  const stampFormat = new Intl.DateTimeFormat("en-SG", {timeZone:zone,day:"numeric",month:"short",year:"numeric",hour:"2-digit",minute:"2-digit",hourCycle:"h23"});
  const shortDayFormat = new Intl.DateTimeFormat("en-SG", {timeZone:"UTC",weekday:"short",day:"numeric",month:"short"});
  const monthFormat = new Intl.DateTimeFormat("en-SG", {timeZone:"UTC",month:"short",year:"numeric"});
  const moneyFormats = new Map();
  const calendarDay = value => new Date(`${value}T00:00:00Z`);
  return {
    day: value => dayFormat.format(calendarDay(value)),
    weekday: value => weekdayFormat.format(calendarDay(value)),
    shortDay: value => shortDayFormat.format(calendarDay(value)),
    month: value => monthFormat.format(calendarDay(value)),
    money(cents, currency) {
      if (!moneyFormats.has(currency)) moneyFormats.set(currency, new Intl.NumberFormat("en-SG", {style:"currency",currency}));
      return moneyFormats.get(currency).format(cents / 100);
    },
    time: instant => timeFormat.format(new Date(instant)),
    stamp: instant => stampFormat.format(new Date(instant)),
    duration(seconds) {
      if (seconds < 60) return `${seconds} sec`;
      const hours = Math.floor(seconds / 3600);
      const minutes = Math.floor((seconds % 3600) / 60);
      return hours ? `${hours} hr${minutes ? ` ${minutes} min` : ""}` : `${minutes} min`;
    },
  };
})();
