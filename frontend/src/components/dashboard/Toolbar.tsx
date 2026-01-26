import { useState } from 'react';
import { Search } from 'lucide-react';
import { Input } from '@/components/ui/input';

interface ToolbarProps {
  onSearch?: (query: string) => void;
}

export function Toolbar({ onSearch }: ToolbarProps) {
  const [searchQuery, setSearchQuery] = useState('');

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && onSearch && searchQuery.trim()) {
      onSearch(searchQuery);
    }
  };

  return (
    <div className="border-b border-border bg-card px-6 py-4">
      <div className="flex flex-wrap items-center gap-4">
        {/* Search Section */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            type="text"
            placeholder="Search parts (e.g., bolt, washer)"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            className="w-64 pl-9"
          />
        </div>
      </div>
    </div>
  );
}
