import discord
from data_manager import dm
import datetime
import time

class Shop:
    """
    Shop for Coins and Gems.
    Now includes limited items, discounts, and more item types!
    """
    def __init__(self, bot):
        self.bot = bot
    
    def get_shop_items(self, guild_id: int):
        return dm.get_guild_data(guild_id, "shop_items", self._default_items())
    
    def _default_items(self):
        return {
            "VIP Role": {"price": 1000, "currency": "coins", "type": "role", "role_id": 0, "stock": -1},
            "Gem Master": {"price": 50, "currency": "gems", "type": "role", "role_id": 0, "stock": -1},
            "Custom Color": {"price": 500, "currency": "coins", "type": "color", "stock": -1},
            "Server Banner": {"price": 1000, "currency": "coins", "type": "banner", "stock": 10},
            "Private Channel": {"price": 2000, "currency": "coins", "type": "channel", "stock": 5}
        }
    
    """Limited Items System"""
    def get_stock(self, guild_id: int, item_name: str) -> int:
        stock_data = dm.get_guild_data(guild_id, "shop_stock", {})
        return stock_data.get(item_name, -1)  # -1 = unlimited
    
    def update_stock(self, guild_id: int, item_name: str, change: int):
        if change == 0:
            return
        
        stock_data = dm.get_guild_data(guild_id, "shop_stock", {})
        
        if item_name not in stock_data:
            stock_data[item_name] = 0
        
        stock_data[item_name] += change
        
        dm.update_guild_data(guild_id, "shop_stock", stock_data)
    
    """Discounts System"""
    def get_active_discounts(self, guild_id: int) -> dict:
        discounts = dm.get_guild_data(guild_id, "shop_discounts", {})
        active = {}
        
        for item_name, discount_data in discounts.items():
            if discount_data.get("expires", 0) > time.time():
                active[item_name] = discount_data
        
        return active
    
    def apply_discount(self, guild_id: int, item_name: str, percent: int, duration_hours: int = 24):
        """Apply discount to item."""
        discounts = dm.get_guild_data(guild_id, "shop_discounts", {})
        
        discounts[item_name] = {
            "percent": percent,
            "original_price": 0,  # Will be set when used
            "expires": time.time() + (duration_hours * 3600)
        }
        
        dm.update_guild_data(guild_id, "shop_discounts", discounts)
    
    def get_discounted_price(self, guild_id: int, item_name: str, base_price: int) -> int:
        """Get price with discount applied."""
        discounts = self.get_active_discounts(guild_id)
        
        if item_name not in discounts:
            return base_price
        
        discount = discounts[item_name]
        percent = discount["percent"]
        
        # Calculate discounted price
        discount_amount = int(base_price * (percent / 100))
        return max(1, base_price - discount_amount)
    
    """Limited Time Offers"""
    LIMITED_OFFERS = {
        "flash_sale": {"duration": 4, "discount": 50, "name": "⚡ Flash Sale"},
        "weekend": {"duration": 48, "discount": 25, "name": "� weekend Deal"},
        "daily": {"duration": 24, "discount": 15, "name": "Daily Deal"}
    }
    
    def start_limited_offer(self, guild_id: int, offer_type: str):
        """Start a limited time offer."""
        if offer_type not in self.LIMITED_OFFERS:
            return
        
        offer = self.LIMITED_OFFERS[offer_type]
        
        # Apply discount to random item
        items = self.get_shop_items(guild_id)
        if not items:
            return
        
        import random
        item_name = random.choice(list(items.keys()))
        
        self.apply_discount(guild_id, item_name, offer["discount"], offer["duration"])
        
        return item_name, offer
    
    """Item Categories"""
    ITEM_CATEGORIES = {
        "roles": {"emoji": "🎭", "description": "Server roles"},
        "colors": {"emoji": "🎨", "description": "Custom colors"},
        "channels": {"emoji": "#️⃣", "description": "Private channels"},
        "banners": {"emoji": "🖼️", "description": "Server banners"},
        "emotes": {"emoji": "😀", "description": "Custom emotes"}
    }
    
    async def show_shop(self, interaction: discord.Interaction, category: str = None):
        guild_id = interaction.guild.id
        items = self.get_shop_items(guild_id)
        discounts = self.get_active_discounts(guild_id)
        
        embed = discord.Embed(title="🛒 Server Shop", color=discord.Color.blue())
        
        for name, data in items.items():
            if category and data.get("category") != category:
                continue
            
            price = data['price']
            currency = "💰 Coins" if data['currency'] == "coins" else "💎 Gems"
            
            # Show stock
            stock = data.get("stock", -1)
            if stock > 0:
                stock_text = f" | Stock: {stock}"
            elif stock == 0:
                stock_text = " | ❌ SOLD OUT"
            else:
                stock_text = ""
            
            # Show discount
            if name in discounts:
                old_price = price
                new_price = self.get_discounted_price(guild_id, name, price)
                price_text = f"~~{old_price}~~ **{new_price}** {currency} 🔥"
            else:
                price_text = f"**{price}** {currency}"
            
            embed.add_field(
                name=f"{name}{stock_text}",
                value=f"Price: {price_text}\nType: {data['type']}",
                inline=True
            )
        
        if discounts:
            embed.set_footer(text="🔥 Limited time offers active!")
        
        await interaction.response.send_message(embed=embed)
    
    async def buy_item(self, interaction: discord.Interaction, item_name: str):
        guild_id = interaction.guild.id
        user_id = interaction.user.id
        items = self.get_shop_items(guild_id)
        
        if item_name not in items:
            return await interaction.response.send_message("Item not found in shop.", ephemeral=True)
        
        item = items[item_name]
        
        # Check stock
        stock = item.get("stock", -1)
        if stock == 0:
            return await interaction.response.send_message("❌ This item is sold out!", ephemeral=True)
        
        # Calculate price with discount
        base_price = item['price']
        price = self.get_discounted_price(guild_id, item_name, base_price)
        currency = item['currency']
        
        # Deduct payment
        if currency == "coins":
            if self.bot.economy.get_coins(guild_id, user_id) < price:
                return await interaction.response.send_message("Not enough coins!", ephemeral=True)
            self.bot.economy.add_coins(guild_id, user_id, -price)
        else:
            if not self.bot.leveling.spend_gems(guild_id, user_id, price):
                return await interaction.response.send_message("Not enough gems!", ephemeral=True)
        
        # Grant item
        if item['type'] == 'role':
            role = interaction.guild.get_role(item['role_id'])
            if role:
                await interaction.user.add_roles(role)
        
        # Decrease stock
        if stock > 0:
            self.update_stock(guild_id, item_name, -1)
        
        # Log purchase
        purchases = dm.get_guild_data(guild_id, "purchases", [])
        purchases.append({
            "user_id": user_id,
            "item": item_name,
            "price": price,
            "timestamp": str(datetime.datetime.now())
        })
        dm.update_guild_data(guild_id, "purchases", purchases)
        
        await interaction.response.send_message(f"✅ Purchased **{item_name}** for {price}!")
    
    """Admin: Add custom items"""
    async def add_item(self, guild_id: int, name: str, price: int, item_type: str, 
                      currency: str = "coins", stock: int = -1):
        items = self.get_shop_items(guild_id)
        
        items[name] = {
            "price": price,
            "currency": currency,
            "type": item_type,
            "stock": stock,
            "added_at": time.time()
        }
        
        dm.update_guild_data(guild_id, "shop_items", items)
    
    """Admin: Remove item"""
    async def remove_item(self, guild_id: int, name: str):
        items = self.get_shop_items(guild_id)
        
        if name in items:
            del items[name]
            dm.update_guild_data(guild_id, "shop_items", items)
    
    """Admin: Set discount"""
    async def set_discount(self, guild_id: int, item_name: str, percent: int, hours: int = 24):
        self.apply_discount(guild_id, item_name, percent, hours)