
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.NUMERIC_STD.ALL;

entity crossbar_map_tri is
  generic (
    N_PINS       : positive := 4;    -- numero di pad fisici
    N_VPIN_TOTAL : positive := 8;    -- numero totale di pin virtuali
    MAP_WIDTH    : positive := 4;    -- bit per mapping
    SYNC_INPUT   : boolean  := true  -- true=sincronizza input, false=input combinatorio
  );
  port (
    clk          : in  std_logic;
    rst_n        : in  std_logic;
    
    -- Mapping: per ogni pin fisico, l'indice del virtual pin
    -- 0 = non mappato, 1..N_VPIN_TOTAL = mappato a vpin (index-1)
    owner_map    : in  std_logic_vector(N_PINS * MAP_WIDTH - 1 downto 0);
    
    -- Output enable per ogni PIN FISICO (NON virtuale!)
    pad_oe       : in  std_logic_vector(N_PINS - 1 downto 0);  -- '1'=pin abilitato, '0'=Hi-Z
    
    -- Virtual pins I/O (tutti i tenant linearizzati)
    vpin_o       : in  std_logic_vector(N_VPIN_TOTAL - 1 downto 0);
    vpin_t       : in  std_logic_vector(N_VPIN_TOTAL - 1 downto 0); -- '0'=drive, '1'=Hi-Z
    vpin_i       : out std_logic_vector(N_VPIN_TOTAL - 1 downto 0);
    
    -- Physical pins
    pad_io       : inout std_logic_vector(N_PINS - 1 downto 0);
    led_oe: out std_logic
  );
end entity;

architecture rtl of crossbar_map_tri is
  constant CHECK_WIDTH : boolean := (2**MAP_WIDTH > N_VPIN_TOTAL);
  constant UNMAPPED  : unsigned(MAP_WIDTH-1 downto 0) := (others => '0'); 
  

  type map_array_t is array(0 to N_PINS-1) of unsigned(MAP_WIDTH-1 downto 0);
  signal map_array : map_array_t;
  signal map_array_reg : map_array_t;  
  signal unique_map_comb : std_logic_vector(N_PINS-1 downto 0);
  signal unique_map : std_logic_vector(N_PINS-1 downto 0);  
  

  signal pad_in_reg : std_logic_vector(N_PINS-1 downto 0);
  
begin

  assert CHECK_WIDTH
    report "MAP_WIDTH troppo piccolo! Deve essere >= log2(N_VPIN_TOTAL+1)"
    severity failure;


  gen_extract: for i in 0 to N_PINS-1 generate
    map_array(i) <= unsigned(owner_map((i+1)*MAP_WIDTH-1 downto i*MAP_WIDTH));
  end generate;
  

  gen_unique: for i in 0 to N_PINS-1 generate
    process(map_array)  
      variable is_unique : std_logic;
    begin
      if map_array(i) > 0 and map_array(i) <= N_VPIN_TOTAL then
        is_unique := '1';
        for j in 0 to N_PINS-1 loop
          if i /= j and map_array(j) = map_array(i) then
            is_unique := '0';
          end if;
        end loop;
        unique_map_comb(i) <= is_unique;
      else
        unique_map_comb(i) <= '0';
      end if;
    end process;
  end generate;
  

  process(clk, rst_n)
  begin
    if rst_n = '0' then
      map_array_reg <= (others => (others => '0'));
      unique_map <= (others => '0');
    elsif rising_edge(clk) then
      map_array_reg <= map_array;
      unique_map <= unique_map_comb; 
    end if;
  end process;
  

  gen_routing: for i in 0 to N_PINS-1 generate
    signal vpin_idx : integer range 0 to N_VPIN_TOTAL-1;
    signal pad_out : std_logic;
    signal pad_drive : std_logic;
  begin
    process(map_array_reg(i), unique_map(i), vpin_o, vpin_t, pad_oe(i))
    begin
      if unique_map(i) = '1' and map_array_reg(i) > 0 and map_array_reg(i) <= N_VPIN_TOTAL then
        vpin_idx <= to_integer(map_array_reg(i)) - 1; 
        pad_out <= vpin_o(vpin_idx);

        pad_drive <= pad_oe(i) and (not vpin_t(vpin_idx));
      else
        vpin_idx <= 0;
        pad_out <= '0';
        pad_drive <= '0';
      end if;
    end process;
    
    -- Output DIRETTO senza registri (latenza zero!)
    pad_io(i) <= pad_out when pad_drive = '1' else 'Z';
  end generate;
  
  -- Input path: opzionalmente sincronizzato
  gen_sync_input: if SYNC_INPUT generate
    process(clk, rst_n)
    begin
      if rst_n = '0' then
        pad_in_reg <= (others => '0');
      elsif rising_edge(clk) then
        pad_in_reg <= pad_io;
      end if;
    end process;
  end generate;
  
  gen_async_input: if not SYNC_INPUT generate
    pad_in_reg <= pad_io; 
  end generate;
  

  process(map_array_reg, pad_in_reg, unique_map)
    variable temp_vpin_i : std_logic_vector(N_VPIN_TOTAL-1 downto 0);
    variable vpin_idx : integer range 0 to N_VPIN_TOTAL-1;
  begin
    temp_vpin_i := (others => '0');
    
    for i in 0 to N_PINS-1 loop
      if unique_map(i) = '1' and map_array_reg(i) > 0 and map_array_reg(i) <= N_VPIN_TOTAL then
        vpin_idx := to_integer(map_array_reg(i)) - 1; 
        temp_vpin_i(vpin_idx) := pad_in_reg(i);
      end if;
    end loop;
    
    vpin_i <= temp_vpin_i;
  end process;

led_oe <= pad_oe(0);

end architecture;
