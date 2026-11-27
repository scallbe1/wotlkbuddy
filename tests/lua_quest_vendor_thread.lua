local f=assert(io.open('addons/WotLKBuddyTelemetry/WotLKBuddyTelemetry.lua','r'))
local src=f:read('*a'); f:close()
local code=assert(src:match('%-%- BEGIN VERIFIED QUEST VENDOR PURCHASE\n(.-)%-%- END VERIFIED QUEST VENDOR PURCHASE'))
local count,price,stock,quantity,cash,combat,dead,extended=0,10,-1,1,20,false,false,false
local guid='0xF130000846000001'
local links={'|Hitem:2324:0|h[Dye]|h','|Hitem:2320:0|h[Coarse Thread]|h'}
local buys={}
SlashCmdList={}
MerchantFrame={selectedTab=1,IsShown=function() return true end}
UnitAffectingCombat=function() return combat end
UnitIsDeadOrGhost=function() return dead end
UnitGUID=function() return guid end
GetItemCount=function() return count end
GetMoney=function() return cash end
GetMerchantNumItems=function() return #links end
GetMerchantItemLink=function(i) return links[i] end
GetMerchantItemInfo=function() return 'Coarse Thread',nil,price,quantity,stock,true,extended end
BuyMerchantItem=function(i,n) table.insert(buys,{i,n}) end
assert(loadstring('local trackedQuestItemId=2320\n'..code))()
local run=SlashCmdList.WOTLKBUDDYBUYTHREAD
run('')
assert(#buys==1 and buys[1][1]==2 and buys[1][2]==1)
count=1; run(''); count=0
price=11; run(''); price=10
quantity=2; run(''); quantity=1
stock=0; run(''); stock=-1
extended=true; run(''); extended=false
cash=9; run(''); cash=20
combat=true; run(''); combat=false
dead=true; run(''); dead=false
guid='0xF130000847000001'; run(''); guid='0xF130000846000001'
MerchantFrame.selectedTab=2; run(''); MerchantFrame.selectedTab=1
links[3]=links[2]; run(''); links[3]=nil
run('unexpected')
assert(#buys==1,'unsafe purchase was authorized')
print('Quest vendor purchase guards verified')
