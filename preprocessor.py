import pandas as pd
import re
from Sastrawi.Stemmer.StemmerFactory import StemmerFactory
from Sastrawi.StopWordRemover.StopWordRemoverFactory import StopWordRemoverFactory

class HoaxDataPreprocessor:
    def __init__(self):
        self.stemmer = StemmerFactory().create_stemmer()
        self.stopword_remover = StopWordRemoverFactory().create_stop_word_remover()
    
    def data_cleaning(self, text):
        if pd.isna(text) or not isinstance(text, str):
            return ""
        
        # Hapus URL
        text = re.sub(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', '', text)
        text = re.sub(r'www\.(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', '', text)
        
        # Hapus label khusus
        labels_to_remove = [
            r'NARASI\s*:', r'\[NARASI\]\s*:', r'Narasi\s*:', 
            r'REFERENSI\s*:', r'PENJELASAN\s*:', r'KATEGORI\s*:',
            r'SUMBER\s*:', r'Editor\s*:', r'Pewarta\s*:',
        ]
        for label in labels_to_remove:
            text = re.sub(label, '', text, flags=re.IGNORECASE)
        
        # Hapus brand cues
        brand_patterns = [
            r'\([A-Z\s]+\)\s*-',
            r'[A-Za-z]+\s*\([A-Z]+\)\s*-',
            r'[A-Z]+\.com[\s\-]*',
        ]
        for pattern in brand_patterns:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)
        
        # Hapus emoji
        emoji_pattern = re.compile("["
            u"\U0001F600-\U0001F64F"
            u"\U0001F300-\U0001F5FF"
            u"\U0001F680-\U0001F6FF"
            u"\U0001F1E0-\U0001F1FF"
            u"\U00002702-\U000027B0"
            u"\U000024C2-\U0001F251"
            "]+", flags=re.UNICODE)
        text = emoji_pattern.sub(r'', text)
        
        # Hapus karakter spesial
        text = re.sub(r'[^a-zA-Z0-9\s]', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        
        return text.strip()
    
    def case_folding(self, text):
        if pd.isna(text) or not isinstance(text, str):
            return ""
        return text.lower()
    
    def normalization(self, text):
        if pd.isna(text) or not isinstance(text, str):
            return ""
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
    
    def stemming(self, text):
        if pd.isna(text) or not isinstance(text, str):
            return ""
        return self.stemmer.stem(text)
    
    def stopword_removal(self, text):
        if pd.isna(text) or not isinstance(text, str):
            return ""
        return self.stopword_remover.remove(text)
    
    def preprocess_pipeline(self, text):
        text = self.data_cleaning(text)
        text = self.case_folding(text)
        text = self.normalization(text)
        text = self.stemming(text)
        text = self.stopword_removal(text)
        return text
