package com.risegram.app;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.graphics.Color;
import android.graphics.Typeface;
import android.content.Context;
import android.content.SharedPreferences;
import android.view.Gravity;
import android.view.View;
import android.widget.*;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.*;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private static final String API = "https://risegram-risegram.up.railway.app";
    private static final String PREFS = "risegram_session";
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private SharedPreferences prefs;
    private LinearLayout root, content;
    private String token = "";
    private TextView balanceView;
    private final Handler balanceHandler = new Handler(Looper.getMainLooper());
    private final Runnable balancePoll = new Runnable() {
        @Override public void run() {
            if (!token.isEmpty() && balanceView != null) {
                api("GET", "/api/me", token, null, (res, err) -> {
                    JSONObject u = res.optJSONObject("user");
                    if (err == null && u != null && balanceView != null) {
                        balanceView.setText("⭐ RiseStars   " + number(u.optDouble("risestars")) + "\n💎 Gram   " + number(u.optDouble("gram_balance")));
                    }
                });
            }
            balanceHandler.postDelayed(this, 5000);
        }
    };

    private int bg = Color.rgb(13, 19, 29), panel = Color.rgb(24, 33, 47);
    private int text = Color.rgb(238, 243, 250), muted = Color.rgb(150, 164, 183);
    private int accent = Color.rgb(87, 165, 255), green = Color.rgb(72, 202, 139);

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        token = prefs.getString("token", "");
        getWindow().setStatusBarColor(bg);
        getWindow().setNavigationBarColor(bg);
        render();
        if (!token.isEmpty()) loadProfile();
    }

    private void render() {
        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(bg);
        TextView brand = new TextView(this);
        brand.setText("RiseGram");
        brand.setTextColor(text);
        brand.setTextSize(25);
        brand.setTypeface(null, Typeface.BOLD);
        brand.setPadding(dp(20), dp(22), dp(20), dp(14));
        root.addView(brand);
        ScrollView scroll = new ScrollView(this);
        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(dp(16), dp(4), dp(16), dp(28));
        scroll.addView(content);
        root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(root);
        if (token.isEmpty()) showLogin(); else showLoading("Загружаем аккаунт…");
    }

    private void showLogin() {
        content.removeAllViews();
        title("Вход в RiseGram");
        TextView hint = label("1. Откройте @risegrambot и нажмите /start\n2. Введите постоянный номер из раздела «Мой номер»\n3. Нажмите «Получить код» и проверьте личные сообщения бота\n4. Введите одноразовый код и войдите\n\nМожно добавить несколько аккаунтов и переключаться между ними");
        content.addView(hint);
        if (!token.isEmpty()) {
            Button back = button("← Вернуться в текущий аккаунт", panel);
            content.addView(back);
            back.setOnClickListener(v -> loadProfile());
        }
        EditText phone = field("Постоянный номер из @risegrambot");
        phone.setInputType(android.text.InputType.TYPE_CLASS_PHONE);
        content.addView(phone);
        EditText code = field("Одноразовый код из бота");
        code.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
        content.addView(code);
        Button request = button("Получить код", accent);
        content.addView(request);
        Button enter = button("Войти", green);
        content.addView(enter);
        request.setOnClickListener(v -> {
            String p = phone.getText().toString().trim();
            if (p.replaceAll("\\D", "").length() < 7) { toast("Введите постоянный номер из @risegrambot полностью"); return; }
            request.setEnabled(false);
            api("POST", "/api/auth/request", null, json("phone", p), (res, err) -> {
                request.setEnabled(true);
                if (err != null) toast(err);
                else toast("Проверьте личные сообщения @risegrambot — там должен быть код входа");
            });
        });
        enter.setOnClickListener(v -> {
            String p = phone.getText().toString().trim();
            String c = code.getText().toString().trim();
            if (p.replaceAll("\\D", "").length() < 7 || c.length() != 5) { toast("Проверьте номер и 5-значный код из бота"); return; }
            enter.setEnabled(false);
            api("POST", "/api/auth/verify", null, json("phone", p, "code", c), (res, err) -> {
                enter.setEnabled(true);
                if (err != null) { toast(err); return; }
                String t = res.optString("token", "");
                if (t.isEmpty()) { toast("Не удалось войти"); return; }
                token = t;
                JSONObject signedUser = res.optJSONObject("user");
                saveAccount(token, signedUser);
                showLoading("Вход выполнен");
                loadProfile();
            });
        });
    }

    private JSONArray getAccounts() {
        try { return new JSONArray(prefs.getString("accounts", "[]")); }
        catch (Exception e) { return new JSONArray(); }
    }

    private void saveAccount(String session, JSONObject user) {
        if (session == null || session.isEmpty() || user == null) return;
        String riseId = user.optString("rise_id", "");
        JSONArray old = getAccounts();
        JSONArray updated = new JSONArray();
        try {
            for (int i = 0; i < old.length(); i++) {
                JSONObject item = old.optJSONObject(i);
                if (item != null && !riseId.equals(item.optString("rise_id"))) updated.put(item);
            }
            JSONObject account = new JSONObject();
            account.put("rise_id", riseId);
            account.put("name", user.optString("name", "RiseGram"));
            account.put("username", user.optString("username", ""));
            account.put("token", session);
            updated.put(account);
        } catch (Exception ignored) {}
        prefs.edit().putString("accounts", updated.toString())
            .putString("active_rise_id", riseId).putString("token", session).apply();
    }

    private void showAccounts() {
        JSONArray accounts = getAccounts();
        if (accounts.length() == 0) {
            toast("Сохранённых аккаунтов пока нет");
            return;
        }
        String[] labels = new String[accounts.length()];
        for (int i = 0; i < accounts.length(); i++) {
            JSONObject a = accounts.optJSONObject(i);
            if (a == null) { labels[i] = "Аккаунт"; continue; }
            labels[i] = a.optString("name", "RiseGram") + " · " +
                (a.optString("username").isEmpty() ? "без username" : "@" + a.optString("username")) +
                " · Rise ID " + a.optString("rise_id");
            if (a.optString("rise_id").equals(prefs.getString("active_rise_id", "")))
                labels[i] = "✓ " + labels[i];
        }
        new android.app.AlertDialog.Builder(this).setTitle("Ваши аккаунты")
            .setItems(labels, (dialog, which) -> {
                JSONObject selected = accounts.optJSONObject(which);
                if (selected == null) return;
                token = selected.optString("token", "");
                prefs.edit().putString("token", token)
                    .putString("active_rise_id", selected.optString("rise_id", "")).apply();
                loadProfile();
            })
            .setPositiveButton("Готово", null).show();
    }

    private void removeCurrentAccount() {
        String active = prefs.getString("active_rise_id", "");
        JSONArray old = getAccounts();
        JSONArray remaining = new JSONArray();
        for (int i = 0; i < old.length(); i++) {
            JSONObject a = old.optJSONObject(i);
            if (a != null && !active.equals(a.optString("rise_id"))) remaining.put(a);
        }
        if (remaining.length() > 0) {
            JSONObject next = remaining.optJSONObject(0);
            token = next == null ? "" : next.optString("token", "");
            prefs.edit().putString("accounts", remaining.toString())
                .putString("token", token)
                .putString("active_rise_id", next == null ? "" : next.optString("rise_id", "")).apply();
            loadProfile();
        } else {
            token = "";
            prefs.edit().putString("accounts", "[]").remove("token").remove("active_rise_id").apply();
            showLogin();
        }
    }

    private void showLoading(String message) {
        content.removeAllViews();
        title(message);
        ProgressBar bar = new ProgressBar(this);
        content.addView(bar);
    }

    private void loadProfile() {
        api("GET", "/api/me", token, null, (res, err) -> {
            if (err != null) {
                token = "";
                prefs.edit().remove("token").apply();
                showLogin();
                toast("Сессия истекла — войдите снова");
                return;
            }
            JSONObject user = res.optJSONObject("user");
            if (user == null) { showLogin(); return; }
            if (getAccounts().length() == 0 || !prefs.getString("active_rise_id", "").equals(user.optString("rise_id"))) {
                saveAccount(token, user);
            }
            showHome(user);
        });
    }

    private void showHome(JSONObject user) {
        content.removeAllViews();
        title(user.optString("name", "Профиль"));
        card("@" + user.optString("username", "user") + "   ·   Rise ID " + user.optString("rise_id"));
        balanceView = label("⭐ RiseStars   " + number(user.optDouble("risestars")) + "\\n💎 Gram   " + number(user.optDouble("gram_balance")));
        balanceView.setTextColor(text);
        balanceView.setTextSize(16);
        balanceView.setPadding(dp(15), dp(15), dp(15), dp(15));
        balanceView.setBackgroundColor(panel);
        LinearLayout.LayoutParams balanceLp = new LinearLayout.LayoutParams(-1, -2);
        balanceLp.bottomMargin = dp(10);
        content.addView(balanceView, balanceLp);
        rowButtons("Обновить", "Маркет", "Инвентарь");
        Button accounts = button("👤 Аккаунты · переключить / добавить", panel);
        content.addView(accounts);
        accounts.setOnClickListener(v -> showAccounts());
        Button addAccount = button("＋ Добавить ещё аккаунт", accent);
        content.addView(addAccount);
        addAccount.setOnClickListener(v -> showLogin());
        Button chats = button("💬 Чаты", green);
        content.addView(chats);
        chats.setOnClickListener(v -> loadChats());
        Button spaces = button("👥 Группы и каналы", green);
        content.addView(spaces);
        spaces.setOnClickListener(v -> loadSpaces());
        Button find = button("🔎 Найти пользователя по Rise ID", panel);
        content.addView(find);
        find.setOnClickListener(v -> showFindUser());
        Button refresh = button("↻ Обновить баланс", accent);
        content.addView(refresh);
        refresh.setOnClickListener(v -> loadProfile());
        Button market = button("🛒 Открыть маркет", green);
        content.addView(market);
        market.setOnClickListener(v -> loadMarket());
        Button inventory = button("🎒 Мои NFT", accent);
        content.addView(inventory);
        inventory.setOnClickListener(v -> loadInventory());
        Button games = button("🎮 Статистика игр", panel);
        content.addView(games);
        games.setOnClickListener(v -> loadGameStats());
        Button logout = button("Выйти", panel);
        content.addView(logout);
        logout.setOnClickListener(v -> removeCurrentAccount());
    }

    private void showFindUser() {
        EditText query = field("Rise ID или имя пользователя");
        new android.app.AlertDialog.Builder(this).setTitle("Найти в RiseGram")
            .setView(query).setNegativeButton("Отмена", null)
            .setPositiveButton("Найти", (d, w) -> {
                String q = query.getText().toString().trim();
                if (q.length() < 2) { toast("Введите минимум 2 символа"); return; }
                api("GET", "/api/users/search?q=" + android.net.Uri.encode(q), token, null, (res, err) -> {
                    if (err != null) { toast(err); return; }
                    JSONArray users = res.optJSONArray("users");
                    if (users == null || users.length() == 0) { toast("Пользователь не найден"); return; }
                    String[] labels = new String[users.length()];
                    int[] ids = new int[users.length()];
                    for (int i = 0; i < users.length(); i++) {
                        JSONObject u = users.optJSONObject(i);
                        ids[i] = u == null ? 0 : u.optInt("rise_id");
                        labels[i] = (u == null ? "Пользователь" : u.optString("name")) + " · Rise ID " + ids[i];
                    }
                    new android.app.AlertDialog.Builder(this).setTitle("Результаты")
                        .setItems(labels, (dialog, which) -> openChat(ids[which], labels[which]))
                        .setNegativeButton("Закрыть", null).show();
                });
            }).show();
    }

    private void loadChats() {
        showLoading("Загружаем чаты…");
        api("GET", "/api/chats", token, null, (res, err) -> {
            if (err != null) { toast(err); loadProfile(); return; }
            content.removeAllViews();
            title("Сообщения");
            Button search = button("＋ Новый чат по Rise ID", green);
            content.addView(search);
            search.setOnClickListener(v -> showFindUser());
            JSONArray chats = res.optJSONArray("chats");
            if (chats == null || chats.length() == 0) content.addView(label("Пока нет диалогов — найдите пользователя по Rise ID"));
            else for (int i = 0; i < chats.length(); i++) {
                JSONObject c = chats.optJSONObject(i);
                if (c == null) continue;
                JSONObject u = c.optJSONObject("user");
                if (u == null) continue;
                int rid = u.optInt("rise_id");
                String name = u.optString("name", "Пользователь");
                String last = c.optString("last_message", "");
                Button item = button(name + " · " + rid + "\\n" + last, panel);
                content.addView(item);
                item.setOnClickListener(v -> openChat(rid, name));
            }
            Button back = button("Назад", panel);
            content.addView(back);
            back.setOnClickListener(v -> loadProfile());
        });
    }

    private void openChat(int riseId, String titleText) {
        showLoading("Открываем диалог…");
        api("GET", "/api/chats/" + riseId + "/messages", token, null, (res, err) -> {
            if (err != null) { toast(err); loadChats(); return; }
            content.removeAllViews();
            title(titleText);
            JSONArray messages = res.optJSONArray("messages");
            if (messages != null) for (int i = 0; i < messages.length(); i++) {
                JSONObject m = messages.optJSONObject(i);
                if (m == null) continue;
                String prefix = m.optBoolean("from_me") ? "Вы: " : "Собеседник: ";
                card(prefix + m.optString("text"));
            }
            EditText input = field("Сообщение");
            input.setSingleLine(false);
            content.addView(input);
            Button send = button("Отправить", green);
            content.addView(send);
            send.setOnClickListener(v -> {
                String message = input.getText().toString().trim();
                if (message.isEmpty()) return;
                send.setEnabled(false);
                api("POST", "/api/chats/" + riseId + "/messages", token, json("text", message), (answer, error) -> {
                    send.setEnabled(true);
                    if (error != null) toast(error);
                    else openChat(riseId, titleText);
                });
            });
            Button refresh = button("Обновить", accent);
            content.addView(refresh);
            refresh.setOnClickListener(v -> openChat(riseId, titleText));
            Button back = button("К списку чатов", panel);
            content.addView(back);
            back.setOnClickListener(v -> loadChats());
        });
    }

    private void loadSpaces() {
        showLoading("Загружаем группы и каналы…");
        api("GET", "/api/spaces", token, null, (res, err) -> {
            if (err != null) { toast(err); loadProfile(); return; }
            content.removeAllViews();
            title("Группы и каналы");
            Button create = button("＋ Создать группу или канал", green);
            content.addView(create);
            create.setOnClickListener(v -> createSpace());
            Button join = button("Вступить по коду приглашения", accent);
            content.addView(join);
            join.setOnClickListener(v -> joinSpace());
            JSONArray items = res.optJSONArray("spaces");
            if (items == null || items.length() == 0) content.addView(label("Вы ещё не состоите в группах и каналах"));
            else for (int i = 0; i < items.length(); i++) {
                JSONObject item = items.optJSONObject(i);
                if (item == null) continue;
                int id = item.optInt("id");
                String name = item.optString("title");
                String type = item.optString("type").equals("channel") ? "Канал" : "Группа";
                Button open = button((type.equals("Канал") ? "📢 " : "👥 ") + name + "\\n" + item.optString("last_message"), panel);
                content.addView(open);
                open.setOnClickListener(v -> openSpace(id, name));
                TextView invite = label("Код приглашения: " + item.optString("invite_code"));
                content.addView(invite);
            }
            Button back = button("Назад", panel);
            content.addView(back);
            back.setOnClickListener(v -> loadProfile());
        });
    }

    private void createSpace() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        EditText name = field("Название");
        EditText description = field("Описание (необязательно)");
        Spinner type = new Spinner(this);
        type.setAdapter(new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item,
            new String[]{"Группа", "Канал"}));
        box.addView(name); box.addView(description); box.addView(type);
        new android.app.AlertDialog.Builder(this).setTitle("Создать пространство")
            .setView(box).setNegativeButton("Отмена", null)
            .setPositiveButton("Создать", (d, w) -> {
                String kind = type.getSelectedItemPosition() == 1 ? "channel" : "group";
                api("POST", "/api/spaces", token,
                    json("title", name.getText().toString().trim(),
                         "description", description.getText().toString().trim(), "type", kind),
                    (res, err) -> {
                        if (err != null) toast(err);
                        else {
                            JSONObject space = res.optJSONObject("space");
                            if (space != null) toast("Создано · код: " + space.optString("invite_code"));
                            loadSpaces();
                        }
                    });
            }).show();
    }

    private void joinSpace() {
        EditText code = field("Код приглашения");
        new android.app.AlertDialog.Builder(this).setTitle("Вступить")
            .setView(code).setNegativeButton("Отмена", null)
            .setPositiveButton("Вступить", (d, w) ->
                api("POST", "/api/spaces/join", token, json("invite_code", code.getText().toString().trim()),
                    (res, err) -> {
                        if (err != null) toast(err);
                        else { toast("Вы вступили"); loadSpaces(); }
                    })).show();
    }

    private void openSpace(int spaceId, String name) {
        showLoading("Открываем…");
        api("GET", "/api/spaces/" + spaceId + "/messages", token, null, (res, err) -> {
            if (err != null) { toast(err); loadSpaces(); return; }
            content.removeAllViews();
            title(name);
            JSONObject space = res.optJSONObject("space");
            if (space != null && "owner".equals(space.optString("role")))
                card("Код приглашения: " + space.optString("invite_code"));
            JSONArray messages = res.optJSONArray("messages");
            if (messages != null) for (int i = 0; i < messages.length(); i++) {
                JSONObject msg = messages.optJSONObject(i);
                if (msg != null) card(msg.optString("sender") + " · " + msg.optString("rise_id") + "\\n" + msg.optString("text"));
            }
            EditText input = field("Сообщение");
            content.addView(input);
            Button send = button("Отправить", green);
            content.addView(send);
            send.setOnClickListener(v -> {
                String body = input.getText().toString().trim();
                if (body.isEmpty()) return;
                send.setEnabled(false);
                api("POST", "/api/spaces/" + spaceId + "/messages", token, json("text", body), (answer, error) -> {
                    send.setEnabled(true);
                    if (error != null) toast(error);
                    else openSpace(spaceId, name);
                });
            });
            Button refresh = button("Обновить", accent);
            content.addView(refresh);
            refresh.setOnClickListener(v -> openSpace(spaceId, name));
            Button back = button("К списку", panel);
            content.addView(back);
            back.setOnClickListener(v -> loadSpaces());
        });
    }

    private void loadInventory() {
        showLoading("Инвентарь");
        api("GET", "/api/inventory", token, null, (res, err) -> {
            if (err != null) { toast(err); loadProfile(); return; }
            content.removeAllViews();
            title("Мои NFT и номера");
            JSONArray items = res.optJSONArray("items");
            if (items == null || items.length() == 0) content.addView(label("Инвентарь пока пуст"));
            else for (int i = 0; i < items.length(); i++) {
                JSONObject item = items.optJSONObject(i);
                if (item == null) continue;
                final int itemId = item.optInt("id");
                final String itemType = item.optString("type");
                final String itemValue = item.optString("value");
                card((itemType.equals("number") ? "📞 " : "🏷 @") + itemValue + "   ·   #" + itemId);
                if (itemType.equals("username")) {
                    Button activate = button("Сделать @" + itemValue + " активным", accent);
                    content.addView(activate);
                    activate.setOnClickListener(v -> {
                        activate.setEnabled(false);
                        api("POST", "/api/inventory/" + itemId + "/activate", token, new JSONObject(), (answer, error) -> {
                            if (error != null) { activate.setEnabled(true); toast(error); }
                            else { toast("Юзернейм активирован"); loadInventory(); }
                        });
                    });
                }
            }
            Button sell = button("Выставить NFT на маркет", green);
            content.addView(sell);
            sell.setOnClickListener(v -> showSellDialog());
            Button back = button("Назад", panel);
            content.addView(back);
            back.setOnClickListener(v -> loadProfile());
        });
    }

    private void showSellDialog() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        EditText itemId = field("ID NFT из инвентаря");
        EditText price = field("Цена в RiseStars");
        box.addView(itemId); box.addView(price);
        new android.app.AlertDialog.Builder(this).setTitle("Выставить NFT")
            .setView(box).setNegativeButton("Отмена", null)
            .setPositiveButton("Выставить", (d, w) -> {
                try {
                    JSONObject body = new JSONObject();
                    body.put("item_id", Integer.parseInt(itemId.getText().toString().trim()));
                    body.put("price", Double.parseDouble(price.getText().toString().trim()));
                    api("POST", "/api/market", token, body, (res, err) -> {
                        if (err != null) toast(err); else { toast("Лот выставлен"); loadInventory(); }
                    });
                } catch (Exception e) { toast("Проверьте ID и цену"); }
            }).show();
    }

    private void loadMarket() {
        showLoading("Загружаем маркет…");
        api("GET", "/api/market?limit=40", token, null, (res, err) -> {
            if (err != null) { toast(err); loadProfile(); return; }
            content.removeAllViews();
            title("Маркет RiseGram");
            card("Баланс: " + number(res.optDouble("balance")) + " RiseStars");
            JSONArray items = res.optJSONArray("items");
            if (items == null || items.length() == 0) content.addView(label("Активных лотов пока нет"));
            else for (int i = 0; i < items.length(); i++) {
                JSONObject item = items.optJSONObject(i);
                if (item == null) continue;
                final int id = item.optInt("id");
                String value = item.optString("value");
                String kind = item.optString("type");
                LinearLayout block = new LinearLayout(this);
                block.setOrientation(LinearLayout.VERTICAL);
                block.setPadding(dp(14), dp(12), dp(14), dp(12));
                block.setBackgroundColor(panel);
                TextView info = label((kind.equals("number") ? "📞 " : "🏷 @") + value + "\n" + number(item.optDouble("price")) + " RiseStars");
                block.addView(info);
                Button buy = button("Купить", green);
                block.addView(buy);
                buy.setOnClickListener(v -> new android.app.AlertDialog.Builder(this)
                    .setTitle("Подтвердить покупку")
                    .setMessage("Купить " + value + " за " + number(item.optDouble("price")) + " RiseStars?")
                    .setNegativeButton("Отмена", null)
                    .setPositiveButton("Купить", (dialog, which) -> {
                        buy.setEnabled(false);
                        api("POST", "/api/market/" + id + "/buy", token, new JSONObject(), (answer, error) -> {
                            if (error != null) { buy.setEnabled(true); toast(error); }
                            else { toast("NFT куплен и добавлен в инвентарь"); loadMarket(); }
                        });
                    }).show());
                LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
                lp.bottomMargin = dp(10);
                content.addView(block, lp);
            }
            Button back = button("Назад", panel);
            content.addView(back);
            back.setOnClickListener(v -> loadProfile());
        });
    }

    private void loadGameStats() {
        showLoading("Статистика игр");
        api("GET", "/api/games/stats", token, null, (res, err) -> {
            if (err != null) { toast(err); loadProfile(); return; }
            JSONObject stats = res.optJSONObject("stats");
            content.removeAllViews();
            title("Игры");
            if (stats != null) card("Игр: " + stats.optInt("played") + "\nПобед: " + stats.optInt("wins") +
                "\nСтавки: " + number(stats.optDouble("bet")) + "\nВыплаты: " + number(stats.optDouble("paid")));
            JSONObject u = res.optJSONObject("user");
            if (u != null) card("⭐ " + number(u.optDouble("risestars")) + "   ·   💎 " + number(u.optDouble("gram_balance")));
            Button back = button("Назад", panel);
            content.addView(back);
            back.setOnClickListener(v -> loadProfile());
        });
    }

    private interface Callback { void done(JSONObject result, String error); }

    private void api(String method, String path, String bearer, JSONObject body, Callback callback) {
        io.execute(() -> {
            HttpURLConnection conn = null;
            try {
                conn = (HttpURLConnection) new URL(API + path).openConnection();
                conn.setRequestMethod(method);
                conn.setConnectTimeout(12000);
                conn.setReadTimeout(15000);
                conn.setRequestProperty("Accept", "application/json");
                if (bearer != null && !bearer.isEmpty()) conn.setRequestProperty("Authorization", "Bearer " + bearer);
                if (body != null) {
                    conn.setDoOutput(true);
                    conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                    try (OutputStream os = conn.getOutputStream()) {
                        os.write(body.toString().getBytes(StandardCharsets.UTF_8));
                    }
                }
                int status = conn.getResponseCode();
                InputStream stream = status >= 400 ? conn.getErrorStream() : conn.getInputStream();
                StringBuilder raw = new StringBuilder();
                if (stream != null) try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
                    String line; while ((line = reader.readLine()) != null) raw.append(line);
                }
                JSONObject result = raw.length() == 0 ? new JSONObject() : new JSONObject(raw.toString());
                String error = status >= 400 ? result.optString("message", result.optString("error", "HTTP " + status)) : null;
                runOnUiThread(() -> callback.done(result, error));
            } catch (Exception e) {
                runOnUiThread(() -> callback.done(new JSONObject(), "Нет соединения с сервером"));
            } finally { if (conn != null) conn.disconnect(); }
        });
    }

    private JSONObject json(String... pairs) {
        JSONObject obj = new JSONObject();
        try { for (int i = 0; i + 1 < pairs.length; i += 2) obj.put(pairs[i], pairs[i + 1]); }
        catch (Exception ignored) {}
        return obj;
    }

    private void title(String value) {
        TextView v = label(value);
        v.setTextSize(21);
        v.setTypeface(null, Typeface.BOLD);
        v.setTextColor(text);
        v.setPadding(0, dp(12), 0, dp(12));
        content.addView(v);
    }

    private void card(String value) {
        TextView v = label(value);
        v.setTextColor(text);
        v.setTextSize(16);
        v.setPadding(dp(15), dp(15), dp(15), dp(15));
        v.setBackgroundColor(panel);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.bottomMargin = dp(10);
        content.addView(v, lp);
    }

    private TextView label(String value) {
        TextView v = new TextView(this);
        v.setText(value);
        v.setTextColor(muted);
        v.setTextSize(15);
        v.setPadding(0, dp(7), 0, dp(7));
        return v;
    }

    private EditText field(String hint) {
        EditText e = new EditText(this);
        e.setSingleLine(true);
        e.setHint(hint);
        e.setHintTextColor(muted);
        e.setTextColor(text);
        e.setPadding(dp(14), dp(10), dp(14), dp(10));
        e.setBackgroundColor(panel);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.bottomMargin = dp(10);
        e.setLayoutParams(lp);
        return e;
    }

    private Button button(String value, int color) {
        Button b = new Button(this);
        b.setText(value);
        b.setTextColor(Color.WHITE);
        b.setAllCaps(false);
        b.setBackgroundTintList(android.content.res.ColorStateList.valueOf(color));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.bottomMargin = dp(9);
        b.setLayoutParams(lp);
        return b;
    }

    private void rowButtons(String a, String b, String c) { /* navigation is rendered as full-width actions below */ }
    private String number(double n) { return n == Math.rint(n) ? String.format(java.util.Locale.US, "%.0f", n) : String.format(java.util.Locale.US, "%.2f", n); }
    private int dp(int n) { return (int) (n * getResources().getDisplayMetrics().density + 0.5f); }
    private void toast(String value) { Toast.makeText(this, value, Toast.LENGTH_LONG).show(); }

    @Override protected void onResume() {
        super.onResume();
        balanceHandler.removeCallbacks(balancePoll);
        balanceHandler.postDelayed(balancePoll, 5000);
    }

    @Override protected void onPause() {
        balanceHandler.removeCallbacks(balancePoll);
        super.onPause();
    }

    @Override protected void onDestroy() { super.onDestroy(); io.shutdown(); }
}
