package rpc

import (
    "context"
    "fmt"
    "time"

    "github.com/jackc/pgx/v5/pgxpool"
    "go.uber.org/zap"
    "telesrv/internal/domain"
)

const rgPermitOwner int64 = 1780243200
const rgPermitRecipient int64 = 1780243207
const rgPermitBot int64 = 1780243214

type rgPermitItem struct { id, price int64; text string }
var rgPermitItems = []rgPermitItem{
    {9000000000000136, 8000, "О"},
    {9000000000000136, 8000, "О"},
    {9000000000000134, 6666, ""},
    {9000000000000134, 6666, ""},
    {9000000000000134, 6666, ""},
}

// Only the exact fixed, native-user-confirmed order is accepted.
func (r *Router) RunRiseGramGiftPermits(ctx context.Context, pool *pgxpool.Pool, log *zap.Logger) {
    ticker := time.NewTicker(3*time.Second)
    defer ticker.Stop()
    for {
        select {
        case <-ctx.Done(): return
        case <-ticker.C:
            if err := r.rgProcessPermit(ctx,pool); err != nil { log.Warn("Gift permit processing unavailable",zap.Error(err)) }
        }
    }
}

func (r *Router) rgProcessPermit(parent context.Context, pool *pgxpool.Pool) error {
    ctx,cancel:=context.WithTimeout(parent,90*time.Second)
    defer cancel()
    if r==nil || r.deps.Gifts==nil || r.deps.Users==nil { return nil }
    var exists bool
    if err:=pool.QueryRow(ctx,"SELECT to_regclass('public.risegram_owner_gift_permits') IS NOT NULL").Scan(&exists); err!=nil || !exists { return err }
    var token string
    var expires, confirmedAt time.Time
    // Native private callback is the consent, not an admin flag or bot token.
    rows,err:=pool.Query(ctx,`UPDATE risegram_owner_gift_permits p
       SET status='running',confirmed_update_id=q.update_id
       FROM (
         SELECT a.token,u.id AS update_id,u.created_at AS confirmed_at
         FROM risegram_owner_gift_permits a
         JOIN LATERAL (
           SELECT id,created_at FROM bot_api_updates
           WHERE bot_user_id=$1 AND update_kind='callback_query'
             AND callback_user_id=$2 AND peer_type='user' AND peer_id=$2
             AND callback_data=convert_to('rgpermit:'||a.token,'UTF8')
             AND created_at BETWEEN a.created_at AND a.expires_at
           ORDER BY id LIMIT 1
         ) u ON true
         WHERE a.status='pending' AND a.expires_at>now()
         ORDER BY a.created_at LIMIT 1 FOR UPDATE OF a SKIP LOCKED
       ) q WHERE p.token=q.token AND p.status='pending'
       RETURNING p.token,p.expires_at,q.confirmed_at`,rgPermitBot,rgPermitOwner)
    if err!=nil { return err }
    if !rows.Next() { err=rows.Err();rows.Close();return err }
    err=rows.Scan(&token,&expires,&confirmedAt);rows.Close()
    if err!=nil { return err }
    count:=0
    fail:=""
    if len(token)!=32 { fail="Некорректное разрешение" }
    var identities int
    if fail=="" {
        err=pool.QueryRow(ctx,`SELECT count(*) FROM peer_usernames p JOIN users u ON u.id=p.peer_id
           WHERE p.peer_type='user' AND p.active AND u.deleted_at IS NULL AND NOT u.is_bot
           AND ((u.id=$1 AND p.username_lower='owner') OR (u.id=$2 AND p.username_lower='paths'))`,rgPermitOwner,rgPermitRecipient).Scan(&identities)
        if err!=nil || identities!=2 { fail="Аккаунты изменились" }
    }
    if fail=="" {
        var balance int64
        err=pool.QueryRow(ctx,"SELECT balance FROM stars_balances WHERE user_id=$1",rgPermitOwner).Scan(&balance)
        if err!=nil || balance<35998 { fail="Недостаточно RiseStars" }
    }
    if fail=="" {
        for _, item:=range []struct{id,price int64; count int}{{9000000000000136,8000,2},{9000000000000134,6666,3}} {
            g,ok,e:=r.deps.Gifts.GiftByID(ctx,item.id)
            if e!=nil || !ok || g.Stars!=item.price || g.SoldOut || (g.Limited && int(g.AvailabilityRemains)<item.count) { fail="Цена или наличие изменились";break }
        }
    }
    if fail=="" {
        // Start 15 seconds after the native owner's confirmation, not after
        // the bot's response or the worker polling interval.
        if wait := time.Until(confirmedAt.Add(15*time.Second)); wait > 0 {
            if e := rgPermitWait(ctx,wait); e != nil { fail="Отправка прервана" }
        }
        for i,item:=range rgPermitItems {
            if fail!="" {break}
            if i>0 {
                if e := rgPermitWait(ctx,1800*time.Millisecond); e != nil {fail="Отправка прервана";break}
            }
            if time.Now().After(expires) { fail="Разрешение истекло";break }
            if e:=r.rgPermitPurchase(ctx,item,fmt.Sprintf("rg-permit:%s:%d",token,i)); e!=nil {
                fail="Покупка не подтверждена; проверь отправленные подарки"
                break
            }
            count++
            _,_=pool.Exec(ctx,"UPDATE risegram_owner_gift_permits SET sent_count=$2 WHERE token=$1",token,count)
        }
    }
    var actual int
    if e:=pool.QueryRow(ctx,`SELECT count(*) FROM star_gift_purchase_commands
        WHERE buyer_user_id=$1 AND command_key LIKE $2 AND recipient_peer_id=$3 AND recipient_peer_type='user'`,rgPermitOwner,"rg-permit:"+token+":%",rgPermitRecipient).Scan(&actual); e==nil { count=actual }
    status:="failed"
    if count==5 {status="completed";fail=""} else if count>0 {status="partial"}
    _,err=pool.Exec(ctx,"UPDATE risegram_owner_gift_permits SET status=$2,sent_count=$3,error=$4 WHERE token=$1",token,status,count,fail)
    return err
}

func rgPermitWait(ctx context.Context, delay time.Duration) error {
    timer := time.NewTimer(delay)
    defer timer.Stop()
    select {
    case <-ctx.Done(): return ctx.Err()
    case <-timer.C: return nil
    }
}

func (r *Router) rgPermitPurchase(ctx context.Context,item rgPermitItem,key string) error {
    buyer,found,err:=r.deps.Users.ByID(ctx,rgPermitOwner,rgPermitOwner)
    if err!=nil || !found || buyer.Bot || buyer.Deleted {return fmt.Errorf("invalid buyer")}
    to:=domain.Peer{Type:domain.PeerTypeUser,ID:rgPermitRecipient}
    if err:=r.botAPIStarGiftRecipient(ctx,buyer.ID,to);err!=nil {return err}
    g,found,err:=r.deps.Gifts.GiftByID(ctx,item.id)
    if err!=nil || !found || g.Stars!=item.price {return fmt.Errorf("gift price changed")}
    now:=int(r.clock.Now().Unix())
    premium:=r.viewerPremium(ctx,buyer.ID)
    if g.SoldOut || (g.Limited && g.AvailabilityRemains<=0) || g.LockedUntilDate>now || g.Auction || (g.RequirePremium && !premium) || (g.SupportOnly && !r.viewerSupport(ctx,buyer.ID)) {return fmt.Errorf("gift unavailable")}
    blocked,err:=r.peerBlocksUser(ctx,buyer.ID,to.ID)
    if err!=nil || blocked {return fmt.Errorf("recipient unavailable")}
    unsaved,err:=r.starGiftRecipientUnsaved(ctx,buyer.ID,to)
    if err!=nil{return err}
    purchase:=domain.StarGiftPurchaseRequest{
        BuyerUserID:buyer.ID,BuyerPremium:premium,BuyerIsBot:false,To:to,
        GiftID:g.ID,RevisionID:g.RevisionID,IncludeUpgrade:false,
        Message:item.text,ChargeStars:item.price,CommandKey:key,Date:now,
        RecipientBlocked:blocked,RecipientUnsaved:unsaved,
    }
    if _,found,err:=r.deps.Gifts.SettledStarGiftPurchase(ctx,purchase);err!=nil{return err}else if found{return nil}
    form,err:=r.deps.Gifts.IssuePurchaseForm(ctx,domain.StarGiftPurchaseForm{
        BuyerUserID:buyer.ID,To:to,GiftID:g.ID,RevisionID:g.RevisionID,IncludeUpgrade:false,
        Message:item.text,ChargeStars:item.price,IssuedAt:now,ExpiresAt:now+starGiftPurchaseFormTTLSeconds,
    })
    if err!=nil{return err}
    purchase.FormID=form.FormID
    if _,err=r.deps.Gifts.Purchase(ctx,purchase);err!=nil{return err}
    r.invalidateStarGiftOwner(to)
    return nil
}
