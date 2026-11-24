package rpc

import (
    "context"
    "math/big"
    "github.com/jackc/pgx/v5/pgxpool"
    "telesrv/internal/domain"
)

// This preference belongs to the viewer, not to the collectible or its owner.
// Never use the display conversion in a payment, listing or ledger calculation.
var rgGiftValuePool *pgxpool.Pool

func InitRiseGramGiftValuePreferences(pool *pgxpool.Pool) {
    rgGiftValuePool=pool
}

func rgValueMode(ctx context.Context,userID int64) string {
    if rgGiftValuePool==nil || userID<=0 {return "stars"}
    var mode string
    if err:=rgGiftValuePool.QueryRow(ctx,"SELECT mode FROM risegram_gift_value_preferences WHERE user_id=$1",userID).Scan(&mode);err!=nil {return "stars"}
    return mode
}

func rgDisplayGiftValue(ctx context.Context,userID int64,info domain.StarGiftValueInfo) domain.StarGiftValueInfo {
    // All these fields refer to the same native Stars ledger. RUB values are
    // kopecks (ISO 4217 minor units). Conversion is an estimate, not cash value.
    mode:=rgValueMode(ctx,userID)
    if info.LastSalePrice>0 {info.Value=info.LastSalePrice;info.ValueIsAverage=false}
    if info.Currency!="XTR" && info.LastSalePrice<=0 {
        if info.FloorPrice>0 {info.Value=info.FloorPrice} else {info.Value=info.InitialSaleStars}
    }
    info.Currency="XTR"
    if mode=="stars" {return info}
    var multiplier int64
    switch mode {
    case "telegram": multiplier=132
    case "real": multiplier=139
    default:return info
    }
    scale:=func(v int64) int64 {
        if v<=0 {return 0}
        n:=new(big.Int).Mul(big.NewInt(v),big.NewInt(multiplier))
        if mode=="real" {n.Add(n,big.NewInt(50));n.Quo(n,big.NewInt(100))}
        if !n.IsInt64(){return v}
        return n.Int64()
    }
    info.Currency="RUB"
    info.Value=scale(info.Value)
    info.InitialSalePrice=scale(info.InitialSalePrice)
    info.LastSalePrice=scale(info.LastSalePrice)
    info.AveragePrice=scale(info.AveragePrice)
    info.FloorPrice=scale(info.FloorPrice)
    return info
}
