package jforex;

import com.dukascopy.api.Configurable;
import com.dukascopy.api.IAccount;
import com.dukascopy.api.IBar;
import com.dukascopy.api.IContext;
import com.dukascopy.api.IMessage;
import com.dukascopy.api.IConsole;
import com.dukascopy.api.IStrategy;
import com.dukascopy.api.ITick;
import com.dukascopy.api.Instrument;
import com.dukascopy.api.JFException;
import com.dukascopy.api.Period;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileReader;
import java.io.FileInputStream;
import java.io.InputStreamReader;
import java.io.FileWriter;
import java.io.IOException;
import java.io.PushbackReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardOpenOption;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * JForex strategy that captures XAUUSD BID/ASK M1 bars only around scheduled
 * calendar releases. Capture window: [release - preMinutes, release + postMinutes).
 *
 * Output layout is compatible with the historical Python analyzer:
 *   <outputRoot>/BID/YYYY/MM/XAUUSD_M1_BID_YYYY-MM-DD.csv
 *   <outputRoot>/ASK/YYYY/MM/XAUUSD_M1_ASK_YYYY-MM-DD.csv
 *
 * The CSV "volume" column is the number of received ticks in that minute;
 * it is NOT exchange traded volume. Missing minutes are not synthesized.
 */
public class XauUsdNewsWindowCollector implements IStrategy {

    private static final Instrument TARGET = Instrument.XAUUSD;
    private static final DateTimeFormatter FILE_DATE = DateTimeFormatter.ISO_LOCAL_DATE;
    private static final DateTimeFormatter CSV_TIMESTAMP =
            DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ssXXX", Locale.ROOT).withZone(ZoneOffset.UTC);
    private static final long MINUTE_MS = 60_000L;

    @Configurable(value = "Forex Factory main CSV", description = "Use the current/main cache, not the old scraper archive")
    public File calendarCsv = new File("/opt/xauusd/data/forex_factory_cache.csv");

    @Configurable(value = "Output directory", description = "Root folder; BID/ and ASK/ folders are created below it")
    public File outputRoot = new File("/opt/xauusd/live_quotes");

    @Configurable(value = "Calendar refresh seconds")
    public int calendarRefreshSeconds = 30;

    @Configurable(value = "Minutes before release")
    public int preMinutes = 30;

    @Configurable(value = "Minutes after release")
    public int postMinutes = 120;

    @Configurable(value = "Currencies (comma separated; blank = all)")
    public String currencies = "USD";

    @Configurable(value = "Minimum impact: All / Low / Medium / High")
    public String minimumImpact = "Low";

    @Configurable(value = "Reject 00:00 placeholder times")
    public boolean rejectMidnightTime = true;

    @Configurable(value = "Naive timestamps are UTC")
    public boolean assumeNaiveUtc = false;

    private volatile List<ScheduledEvent> scheduledEvents = Collections.emptyList();
    private ScheduledExecutorService calendarExecutor;
    private IConsole console;
    private final Object barLock = new Object();
    private MinuteBar currentBar;
    private final Set<String> loggedEventIds = new HashSet<String>();
    private final Map<String, Long> lastWrittenBidByDate = new HashMap<String, Long>();
    private final Map<String, Long> lastWrittenAskByDate = new HashMap<String, Long>();
    private volatile boolean stopped = false;

    @Override
    public void onStart(IContext context) throws JFException {
        console = context.getConsole();
        if (!context.isFullAccessGranted()) {
            throw new JFException("XAUUSD collector requires Full Access to read the calendar CSV and write quote files. Grant Full Access and restart the strategy.");
        }
        if (calendarRefreshSeconds < 5) {
            calendarRefreshSeconds = 5;
        }
        if (preMinutes < 0 || postMinutes < 1) {
            throw new JFException("Invalid capture window: preMinutes must be >= 0 and postMinutes >= 1.");
        }

        Set<Instrument> instruments = new HashSet<Instrument>();
        instruments.add(TARGET);
        context.setSubscribedInstruments(instruments, true);

        File bidDir = new File(outputRoot, "BID");
        File askDir = new File(outputRoot, "ASK");
        if ((!bidDir.exists() && !bidDir.mkdirs()) || (!askDir.exists() && !askDir.mkdirs())) {
            throw new JFException("Could not create output directories under " + outputRoot.getAbsolutePath());
        }
        File eventLog = new File(outputRoot, "scheduled_events.csv");
        loadLoggedEventIds(eventLog);

        try {
            refreshCalendar();
        } catch (IOException ex) {
            logError("Initial calendar read failed; scheduler will retry: " + ex.getMessage());
        }
        calendarExecutor = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread thread = new Thread(r, "xauusd-calendar-refresh");
            thread.setDaemon(true);
            return thread;
        });
        calendarExecutor.scheduleWithFixedDelay(new Runnable() {
            @Override
            public void run() {
                if (stopped) return;
                try {
                    refreshCalendar();
                } catch (Exception ex) {
                    logError("Calendar refresh failed: " + ex.getMessage());
                }
            }
        }, calendarRefreshSeconds, calendarRefreshSeconds, TimeUnit.SECONDS);

        console.getOut().println("XAUUSD News Window Collector started.");
        console.getOut().println("Calendar: " + calendarCsv.getAbsolutePath());
        console.getOut().println("Output: " + outputRoot.getAbsolutePath());
        console.getOut().println("Capture window: -" + preMinutes + " / +" + postMinutes + " minutes; currencies=" + currencies);
        console.getOut().println("Loaded scheduled events: " + scheduledEvents.size());
    }

    @Override
    public void onTick(Instrument instrument, ITick tick) throws JFException {
        if (!TARGET.equals(instrument) || tick == null) return;
        synchronized (barLock) {
            long tickTime = tick.getTime();
            long minuteStart = (tickTime / MINUTE_MS) * MINUTE_MS;

            // Complete and persist the previous minute before handling the new tick.
            if (currentBar != null && currentBar.minuteStartMs != minuteStart) {
                flushCurrentBar();
            }

            if (!isInsideAnyWindow(tickTime)) {
                return;
            }

            double bid = tick.getBid();
            double ask = tick.getAsk();
            if (!Double.isFinite(bid) || !Double.isFinite(ask) || bid <= 0 || ask <= 0) return;

            if (currentBar == null) {
                currentBar = new MinuteBar(minuteStart, bid, ask);
            } else {
                currentBar.update(bid, ask);
            }
        }
    }

    private boolean isInsideAnyWindow(long tickTimeMs) {
        List<ScheduledEvent> snapshot = scheduledEvents;
        for (ScheduledEvent event : snapshot) {
            long start = event.time.toEpochMilli() - preMinutes * MINUTE_MS;
            long end = event.time.toEpochMilli() + postMinutes * MINUTE_MS;
            if (tickTimeMs >= start && tickTimeMs < end) return true;
        }
        return false;
    }

    private void refreshCalendar() throws IOException {
        if (calendarCsv == null || !calendarCsv.isFile()) {
            logError("Calendar CSV not found: " + (calendarCsv == null ? "null" : calendarCsv.getAbsolutePath()));
            return;
        }
        Instant now = Instant.now();
        Instant oldest = now.minusSeconds((postMinutes + 5L) * 60L);
        Instant newest = now.plusSeconds(14L * 24L * 60L * 60L);
        List<ScheduledEvent> parsed = new ArrayList<ScheduledEvent>();
        Set<String> seen = new LinkedHashSet<String>();

        try (PushbackReader reader = new PushbackReader(
                new BufferedReader(new InputStreamReader(new FileInputStream(calendarCsv), StandardCharsets.UTF_8)), 1)) {
            List<String> headers = readCsvRecord(reader);
            if (headers == null || headers.isEmpty()) {
                logError("Calendar CSV has no header row: " + calendarCsv.getAbsolutePath());
                return;
            }
            Map<String, Integer> indexes = headerIndexes(headers);
            int eventRows = 0;
            List<String> values;
            while ((values = readCsvRecord(reader)) != null) {
                if (values.size() == 1 && values.get(0).trim().isEmpty()) continue;
                eventRows++;
                String name = value(values, indexes, "Event", "event", "event_name");
                String currency = value(values, indexes, "Currency", "currency").toUpperCase(Locale.ROOT);
                String impact = value(values, indexes, "Impact", "impact");
                String actual = value(values, indexes, "actual", "Actual");
                String forecast = value(values, indexes, "forecast", "Forecast");
                String previous = value(values, indexes, "previous", "Previous");
                String rawTime = value(values, indexes, "datetime_utc", "DateTime", "datetime_local");
                if (rawTime.isEmpty()) continue;

                Instant release = parseInstant(rawTime);
                if (release == null) {
                    // Some older exports only have separate date and time columns.
                    String date = value(values, indexes, "date");
                    String time = value(values, indexes, "time");
                    if (!date.isEmpty() && !time.isEmpty()) release = parseInstant(date + "T" + time);
                }
                if (release == null) continue;
                if (rejectMidnightTime && isSourceMidnight(rawTime)) continue;
                if (release.isBefore(oldest) || release.isAfter(newest)) continue;
                if (!acceptCurrency(currency) || !acceptImpact(impact)) continue;
                if (name.isEmpty()) continue;

                String id = eventId(name, currency, release);
                if (!seen.add(id)) continue;
                parsed.add(new ScheduledEvent(id, release, currency, impact, name, actual, forecast, previous));
            }
            if (parsed.isEmpty()) {
                logInfo("Calendar refreshed: 0 eligible upcoming/recent events from " + eventRows + " rows.");
            } else {
                logInfo("Calendar refreshed: " + parsed.size() + " eligible events from " + eventRows + " rows.");
            }
        }

        Collections.sort(parsed, (a, b) -> a.time.compareTo(b.time));
        scheduledEvents = Collections.unmodifiableList(parsed);
        appendNewEvents(parsed);
    }

    private boolean acceptCurrency(String currency) {
        String filter = currencies == null ? "" : currencies.trim();
        if (filter.isEmpty() || "ALL".equalsIgnoreCase(filter)) return true;
        for (String item : filter.split(",")) {
            if (item.trim().equalsIgnoreCase(currency)) return true;
        }
        return false;
    }

    private boolean acceptImpact(String impact) {
        String filter = minimumImpact == null ? "all" : minimumImpact.trim().toLowerCase(Locale.ROOT);
        if (filter.isEmpty() || "all".equals(filter)) return true;
        int required = impactRank(filter);
        int actual = impactRank(impact);
        return actual >= required;
    }

    private int impactRank(String value) {
        String v = value == null ? "" : value.toLowerCase(Locale.ROOT);
        if (v.contains("high")) return 3;
        if (v.contains("medium")) return 2;
        if (v.contains("low")) return 1;
        return 0;
    }

    private boolean isSourceMidnight(String raw) {
        String value = raw == null ? "" : raw.trim().replace(' ', 'T');
        if (value.endsWith("Z")) value = value.substring(0, value.length() - 1) + "+00:00";
        try {
            OffsetDateTime sourceTime = OffsetDateTime.parse(value);
            return sourceTime.getHour() == 0 && sourceTime.getMinute() == 0 && sourceTime.getSecond() == 0;
        } catch (DateTimeParseException ignored) {
            try {
                LocalDateTime sourceTime = LocalDateTime.parse(value);
                return sourceTime.getHour() == 0 && sourceTime.getMinute() == 0 && sourceTime.getSecond() == 0;
            } catch (DateTimeParseException ignoredAgain) {
                return false;
            }
        }
    }

    private Instant parseInstant(String raw) {
        String value = raw == null ? "" : raw.trim();
        if (value.isEmpty()) return null;
        if (value.endsWith("Z")) value = value.substring(0, value.length() - 1) + "+00:00";
        try {
            return OffsetDateTime.parse(value.replace(' ', 'T')).toInstant();
        } catch (DateTimeParseException ignored) {
            // Continue to local/naive parsing below.
        }
        try {
            return Instant.parse(raw.trim());
        } catch (DateTimeParseException ignored) {
            // Continue to local/naive parsing below.
        }
        if (assumeNaiveUtc) {
            try {
                String local = value.replace(' ', 'T');
                return LocalDateTime.parse(local).toInstant(ZoneOffset.UTC);
            } catch (DateTimeParseException ignored) {
                return null;
            }
        }
        return null;
    }

    private void appendNewEvents(List<ScheduledEvent> events) {
        File file = new File(outputRoot, "scheduled_events.csv");
        for (ScheduledEvent event : events) {
            synchronized (loggedEventIds) {
                if (loggedEventIds.contains(event.id)) continue;
                try {
                    boolean writeHeader = !file.exists() || file.length() == 0;
                    try (BufferedWriter writer = Files.newBufferedWriter(file.toPath(), StandardCharsets.UTF_8,
                            StandardOpenOption.CREATE, StandardOpenOption.APPEND)) {
                        if (writeHeader) {
                            writer.write("event_id,event_utc,currency,impact,event,actual,forecast,previous,window_start_utc,window_end_utc");
                            writer.newLine();
                        }
                        long t = event.time.toEpochMilli();
                        writer.write(csv(event.id) + "," + csv(CSV_TIMESTAMP.format(event.time)) + "," +
                                csv(event.currency) + "," + csv(event.impact) + "," + csv(event.name) + "," +
                                csv(event.actual) + "," + csv(event.forecast) + "," + csv(event.previous) + "," +
                                csv(CSV_TIMESTAMP.format(Instant.ofEpochMilli(t - preMinutes * MINUTE_MS))) + "," +
                                csv(CSV_TIMESTAMP.format(Instant.ofEpochMilli(t + postMinutes * MINUTE_MS))));
                        writer.newLine();
                    }
                    loggedEventIds.add(event.id);
                } catch (IOException ex) {
                    logError("Could not append scheduled event " + event.name + ": " + ex.getMessage());
                }
            }
        }
    }

    private void loadLoggedEventIds(File file) {
        if (!file.isFile()) return;
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(new FileInputStream(file), StandardCharsets.UTF_8))) {
            String line;
            boolean first = true;
            while ((line = reader.readLine()) != null) {
                if (first) { first = false; continue; }
                int comma = line.indexOf(',');
                if (comma > 0) loggedEventIds.add(line.substring(0, comma).replace("\"", ""));
            }
        } catch (IOException ex) {
            logError("Could not read existing scheduled_events.csv: " + ex.getMessage());
        }
    }

    private void flushCurrentBar() {
        if (currentBar == null) return;
        MinuteBar bar = currentBar;
        currentBar = null;
        try {
            writeMinute("BID", bar);
            writeMinute("ASK", bar);
        } catch (IOException ex) {
            logError("Failed to write M1 bar at " + CSV_TIMESTAMP.format(Instant.ofEpochMilli(bar.minuteStartMs)) + ": " + ex.getMessage());
        }
    }

    private void writeMinute(String side, MinuteBar bar) throws IOException {
        Instant instant = Instant.ofEpochMilli(bar.minuteStartMs);
        LocalDate day = instant.atOffset(ZoneOffset.UTC).toLocalDate();
        String date = FILE_DATE.format(day);
        File sideRoot = new File(outputRoot, side);
        File folder = new File(new File(sideRoot, String.format(Locale.ROOT, "%04d", day.getYear())),
                String.format(Locale.ROOT, "%02d", day.getMonthValue()));
        if (!folder.exists() && !folder.mkdirs()) throw new IOException("Cannot create " + folder.getAbsolutePath());
        File file = new File(folder, "XAUUSD_M1_" + side + "_" + date + ".csv");

        Map<String, Long> lastMap = "BID".equals(side) ? lastWrittenBidByDate : lastWrittenAskByDate;
        Long lastWritten = lastMap.get(date);
        if (lastWritten == null) {
            lastWritten = readLastTimestamp(file);
            lastMap.put(date, lastWritten);
        }
        if (bar.minuteStartMs <= lastWritten.longValue()) return; // protect against duplicate append after restart

        boolean writeHeader = !file.exists() || file.length() == 0;
        try (BufferedWriter writer = Files.newBufferedWriter(file.toPath(), StandardCharsets.UTF_8,
                StandardOpenOption.CREATE, StandardOpenOption.APPEND)) {
            if (writeHeader) {
                writer.write("timestamp,open,high,low,close,volume");
                writer.newLine();
            }
            double open = "BID".equals(side) ? bar.bidOpen : bar.askOpen;
            double high = "BID".equals(side) ? bar.bidHigh : bar.askHigh;
            double low = "BID".equals(side) ? bar.bidLow : bar.askLow;
            double close = "BID".equals(side) ? bar.bidClose : bar.askClose;
            writer.write(CSV_TIMESTAMP.format(instant) + "," + open + "," + high + "," + low + "," + close + "," + bar.tickCount);
            writer.newLine();
        }
        lastMap.put(date, bar.minuteStartMs);
    }

    private long readLastTimestamp(File file) {
        if (!file.isFile()) return -1L;
        String last = null;
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(new FileInputStream(file), StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                if (!line.trim().isEmpty() && !line.startsWith("timestamp,")) last = line;
            }
        } catch (IOException ex) {
            logError("Could not inspect existing quote file " + file.getAbsolutePath() + ": " + ex.getMessage());
            return -1L;
        }
        if (last == null) return -1L;
        int comma = last.indexOf(',');
        if (comma < 1) return -1L;
        Instant parsed = parseInstant(last.substring(0, comma));
        return parsed == null ? -1L : (parsed.toEpochMilli() / MINUTE_MS) * MINUTE_MS;
    }

    private void logInfo(String message) {
        if (console != null) console.getOut().println(message);
    }

    private void logError(String message) {
        if (console != null) console.getErr().println(message);
    }

    @Override
    public void onBar(Instrument instrument, Period period, IBar askBar, IBar bidBar) throws JFException {
        // Tick aggregation is used so BID/ASK bars share the same timestamps and tick window.
    }

    @Override
    public void onAccount(IAccount account) throws JFException {
    }

    @Override
    public void onMessage(IMessage message) throws JFException {
    }

    @Override
    public void onStop() throws JFException {
        stopped = true;
        if (calendarExecutor != null) {
            calendarExecutor.shutdownNow();
        }
        synchronized (barLock) {
            flushCurrentBar();
        }
        logInfo("XAUUSD News Window Collector stopped.");
    }

    private static Map<String, Integer> headerIndexes(List<String> headers) {
        Map<String, Integer> indexes = new HashMap<String, Integer>();
        for (int i = 0; i < headers.size(); i++) {
            String key = headers.get(i) == null ? "" : headers.get(i).replace("\uFEFF", "").trim().toLowerCase(Locale.ROOT);
            if (!indexes.containsKey(key)) indexes.put(key, i);
        }
        return indexes;
    }

    private static String value(List<String> row, Map<String, Integer> indexes, String... names) {
        for (String name : names) {
            Integer index = indexes.get(name.toLowerCase(Locale.ROOT));
            if (index != null && index.intValue() < row.size()) {
                String result = row.get(index.intValue());
                if (result != null && !result.trim().isEmpty()) return result.trim();
            }
        }
        return "";
    }

    /** RFC-4180 CSV record reader, including quoted commas and embedded newlines. */
    private static List<String> readCsvRecord(PushbackReader reader) throws IOException {
        List<String> fields = new ArrayList<String>();
        StringBuilder field = new StringBuilder();
        boolean inQuotes = false;
        boolean sawAnything = false;
        int code;
        while ((code = reader.read()) != -1) {
            sawAnything = true;
            char ch = (char) code;
            if (inQuotes) {
                if (ch == '"') {
                    int next = reader.read();
                    if (next == '"') {
                        field.append('"');
                    } else {
                        inQuotes = false;
                        if (next != -1) reader.unread(next);
                    }
                } else {
                    field.append(ch);
                }
            } else if (ch == '"') {
                inQuotes = true;
            } else if (ch == ',') {
                fields.add(field.toString());
                field.setLength(0);
            } else if (ch == '\n') {
                fields.add(field.toString());
                return fields;
            } else if (ch == '\r') {
                int next = reader.read();
                if (next != '\n' && next != -1) reader.unread(next);
                fields.add(field.toString());
                return fields;
            } else {
                field.append(ch);
            }
        }
        if (!sawAnything && fields.isEmpty() && field.length() == 0) return null;
        fields.add(field.toString());
        return fields;
    }

    private static String csv(String value) {
        String v = value == null ? "" : value;
        return "\"" + v.replace("\"", "\"\"") + "\"";
    }

    private static String eventId(String name, String currency, Instant time) {
        String raw = name.trim().toLowerCase(Locale.ROOT).replaceAll("\\s+", " ") + "|" + currency + "|" + time.toString();
        try {
            java.security.MessageDigest digest = java.security.MessageDigest.getInstance("SHA-1");
            byte[] bytes = digest.digest(raw.getBytes(StandardCharsets.UTF_8));
            StringBuilder out = new StringBuilder();
            for (int i = 0; i < 8; i++) out.append(String.format(Locale.ROOT, "%02x", bytes[i]));
            return out.toString();
        } catch (java.security.NoSuchAlgorithmException ex) {
            return Integer.toHexString(raw.hashCode());
        }
    }

    private static final class ScheduledEvent {
        final String id;
        final Instant time;
        final String currency;
        final String impact;
        final String name;
        final String actual;
        final String forecast;
        final String previous;

        ScheduledEvent(String id, Instant time, String currency, String impact,
                       String name, String actual, String forecast, String previous) {
            this.id = id;
            this.time = time;
            this.currency = currency;
            this.impact = impact;
            this.name = name;
            this.actual = actual;
            this.forecast = forecast;
            this.previous = previous;
        }
    }

    private static final class MinuteBar {
        final long minuteStartMs;
        final double bidOpen;
        double bidHigh;
        double bidLow;
        double bidClose;
        final double askOpen;
        double askHigh;
        double askLow;
        double askClose;
        long tickCount;

        MinuteBar(long minuteStartMs, double bid, double ask) {
            this.minuteStartMs = minuteStartMs;
            this.bidOpen = this.bidHigh = this.bidLow = this.bidClose = bid;
            this.askOpen = this.askHigh = this.askLow = this.askClose = ask;
            this.tickCount = 1L;
        }

        void update(double bid, double ask) {
            bidHigh = Math.max(bidHigh, bid);
            bidLow = Math.min(bidLow, bid);
            bidClose = bid;
            askHigh = Math.max(askHigh, ask);
            askLow = Math.min(askLow, ask);
            askClose = ask;
            tickCount++;
        }
    }
}
