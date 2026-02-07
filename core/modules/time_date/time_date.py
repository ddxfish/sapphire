import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class TimeDate:
    """Reports current time and date."""
    
    def process(self, user_input, llm_client=None):
        """Process time/date request."""
        logger.info(f"Time/date request received")
        
        # Get current time and date
        now = datetime.now()
        
        # Clean the input and check for keywords
        input_clean = user_input.lower().strip() if user_input else ""
        
        # First check explicitly for date keywords
        if any(word in input_clean for word in ["date", "day", "today"]):
            logger.info("Date keyword explicitly detected")
            date_str = now.strftime("%A, %B %d, %Y")
            return f"Today is {date_str}."
        
        # Check for "what date" or "what's the date" patterns
        if "what" in input_clean and "date" in input_clean:
            logger.info("'What date' pattern detected")
            date_str = now.strftime("%A, %B %d, %Y")
            return f"Today is {date_str}."
            
        # Check for explicit time request
        if "time" in input_clean or "hour" in input_clean or "clock" in input_clean:
            logger.info("Time keyword detected")
            time_str = now.strftime("%I:%M %p")
            return f"It's {time_str}."
            
        # Default fallback
        logger.info("No specific keywords matched, using default (time)")
        time_str = now.strftime("%I:%M %p")
        return f"It's {time_str}."