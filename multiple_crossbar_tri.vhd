library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.NUMERIC_STD.ALL;
use IEEE.MATH_REAL.ALL;

entity crossbar_map_tri is
  generic (
    N_PINS       : positive := 4;   -- numero di pad fisici
    N_VPIN_TOTAL : positive := 8    -- numero totale di pin virtuali (tutti i tenant)
  );
  port (
    clk          : in  std_logic;
    rst_n        : in  std_logic;
    
    -- Mapping: per ogni pin fisico, l'indice del virtual pin
    -- 0 = non mappato, 1..N_VPIN_TOTAL = mappato a vpin (index-1)
    owner_map    : in  std_logic_vector(N_PINS * integer(ceil(log2(real(N_VPIN_TOTAL+1)))) - 1 downto 0);
    
    -- Virtual pins I/O (tutti i tenant linearizzati)
    vpin_o       : in  std_logic_vector(N_VPIN_TOTAL - 1 downto 0);
    vpin_t       : in  std_logic_vector(N_VPIN_TOTAL - 1 downto 0); -- '0'=drive, '1'=Hi-Z
    vpin_i       : out std_logic_vector(N_VPIN_TOTAL - 1 downto 0);
    
    -- Physical pins
    pad_io       : inout std_logic_vector(N_PINS - 1 downto 0)
  );
end entity;

architecture rtl of crossbar_map_tri is
  constant MAP_WIDTH : positive := integer(ceil(log2(real(N_VPIN_TOTAL+1))));
  constant UNMAPPED  : unsigned(MAP_WIDTH-1 downto 0) := (others => '0');  -- 0 = non mappato
  
  -- Segnali interni
  type map_array_t is array(0 to N_PINS-1) of unsigned(MAP_WIDTH-1 downto 0);
  signal map_array : map_array_t;
  signal unique_map : std_logic_vector(N_PINS-1 downto 0);
  
  -- Anti-glitch
  signal pad_out_reg : std_logic_vector(N_PINS-1 downto 0);
  signal pad_oe_reg  : std_logic_vector(N_PINS-1 downto 0);
  signal pad_in_reg  : std_logic_vector(N_PINS-1 downto 0);
  
begin

  -- Estrai mapping per ogni pin
  gen_extract: for i in 0 to N_PINS-1 generate
    map_array(i) <= unsigned(owner_map((i+1)*MAP_WIDTH-1 downto i*MAP_WIDTH));
  end generate;
  
  -- Calcola unique_map
  gen_unique: for i in 0 to N_PINS-1 generate
    process(map_array)
      variable is_unique : std_logic;
    begin
      if map_array(i) > 0 and map_array(i) <= N_VPIN_TOTAL then  -- Mappato (1..N_VPIN_TOTAL)
        is_unique := '1';
        -- Verifica che nessun altro pin abbia lo stesso mapping
        for j in 0 to N_PINS-1 loop
          if i /= j and map_array(j) = map_array(i) then
            is_unique := '0';
          end if;
        end loop;
        unique_map(i) <= is_unique;
      else
        unique_map(i) <= '0';  -- Non mappato (0) o valore invalido (>N_VPIN_TOTAL)
      end if;
    end process;
  end generate;
  
  -- Logica di routing
  gen_routing: for i in 0 to N_PINS-1 generate
    signal vpin_idx : integer range 0 to N_VPIN_TOTAL-1;
    signal sel_o, sel_t, drive_en : std_logic;
  begin
    process(map_array(i), unique_map(i), vpin_o, vpin_t)
    begin
      if unique_map(i) = '1' and map_array(i) > 0 and map_array(i) <= N_VPIN_TOTAL then
        vpin_idx <= to_integer(map_array(i)) - 1;  -- Converte 1..N a 0..N-1
        sel_o <= vpin_o(vpin_idx);
        sel_t <= vpin_t(vpin_idx);
        drive_en <= not sel_t;  -- Drive se non in tristate
      else
        sel_o <= '0';
        sel_t <= '1';
        drive_en <= '0';
      end if;
    end process;
    
    -- Registri anti-glitch
    process(clk, rst_n)
    begin
      if rst_n = '0' then
        pad_out_reg(i) <= '0';
        pad_oe_reg(i) <= '0';
      elsif rising_edge(clk) then
        pad_out_reg(i) <= sel_o;
        pad_oe_reg(i) <= drive_en;
      end if;
    end process;
    
    -- Output buffer
    pad_io(i) <= pad_out_reg(i) when pad_oe_reg(i) = '1' else 'Z';
  end generate;
  
  -- Input routing
  process(clk, rst_n)
  begin
    if rst_n = '0' then
      pad_in_reg <= (others => '0');
    elsif rising_edge(clk) then
      pad_in_reg <= pad_io;
    end if;
  end process;
  
  -- Route inputs back to virtual pins
  process(map_array, pad_in_reg, unique_map)
    variable temp_vpin_i : std_logic_vector(N_VPIN_TOTAL-1 downto 0);
    variable vpin_idx : integer range 0 to N_VPIN_TOTAL-1;
  begin
    temp_vpin_i := (others => '0');
    
    for i in 0 to N_PINS-1 loop
      if unique_map(i) = '1' and map_array(i) > 0 and map_array(i) <= N_VPIN_TOTAL then
        vpin_idx := to_integer(map_array(i)) - 1;  -- Converte 1..N a 0..N-1
        temp_vpin_i(vpin_idx) := pad_in_reg(i);
      end if;
    end loop;
    
    vpin_i <= temp_vpin_i;
  end process;

end architecture;